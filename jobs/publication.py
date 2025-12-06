import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio, Message
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, Application

from database.connection import db_query
from database.queries.schedules import get_task_schedules
from database.queries.settings import get_user_settings
from jobs.delete import execute_delete_job
from jobs.unpin import execute_unpin_job
from localization.loader import get_text
from utils.logging import logger



def create_single_publication_job(task: dict, channel_id: int, utc_dt: datetime, application: Application) -> Optional[int]:
    """Helper function to create a single publication job in DB and JobQueue"""

    # 1. Insert into DB
    job_data = db_query("""
        INSERT INTO publication_jobs (
            task_id, user_id, channel_id, scheduled_time_utc,
            content_message_id, content_chat_id, pin_duration,
            pin_notify, auto_delete_hours, advertiser_user_id, status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'scheduled')
        RETURNING id
    """, (
        task['id'], task['user_id'], channel_id, utc_dt,
        task['content_message_id'], task['content_chat_id'],
        task['pin_duration'], task['pin_notify'],
        task['auto_delete_hours'], task['advertiser_user_id']
    ), commit=True)

    if job_data and 'id' in job_data:
        job_id = job_data['id']
        job_name = f"pub_{job_id}"

        try:
            # 2. Schedule in Telegram JobQueue
            application.job_queue.run_once(
                execute_publication_job,
                when=utc_dt,
                data={'job_id': job_id},
                name=job_name,
                job_kwargs={'misfire_grace_time': 300}  # 5 minutes grace period
            )

            # 3. Update DB with job name
            db_query(
                "UPDATE publication_jobs SET aps_job_id = %s WHERE id = %s",
                (job_name, job_id),
                commit=True
            )
            logger.info(f"✅ Scheduled job {job_id} at {utc_dt} (channel {channel_id})")
            return job_id

        except Exception as e:
            logger.error(f"❌ Failed to schedule job {job_id} via job_queue: {e}", exc_info=True)
            db_query("UPDATE publication_jobs SET status = 'failed' WHERE id = %s", (job_id,), commit=True)
            return None
    else:
        logger.error(f"Failed to insert publication_job in DB for task {task['id']}")
        return None


async def execute_publication_job(context: ContextTypes.DEFAULT_TYPE):
    """
    EXECUTOR: Publishes post, schedules post-actions, and buffers reports.
    Updated to preserve 'Forwarded from' header for Repost Albums.
    """
    bot = context.bot
    job_id = context.job.data.get('job_id')

    if not job_id:
        try:
            job_id = int(context.job.name.replace('pub_', ''))
        except:
            return

    # 1. Fetch Job info
    job_data = db_query("SELECT * FROM publication_jobs WHERE id = %s AND status = 'scheduled'", (job_id,),
                        fetchone=True)
    if not job_data:
        return

    # 2. Fetch Task info
    task_data = db_query("SELECT * FROM tasks WHERE id = %s", (job_data['task_id'],), fetchone=True)
    if not task_data:
        return

    media_group_json = task_data.get('media_group_data')
    channel_id = job_data['channel_id']
    content_message_id = job_data['content_message_id']
    content_chat_id = job_data['content_chat_id']
    api_disable_notification = not job_data['pin_notify']

    # Variables to track message IDs
    posted_message_id = None
    all_posted_ids = []
    sent_msg_object = None

    try:
        # --- 3. PUBLISHING LOGIC --- #
        # Check if this is a "Repost" (Forward) or "From Bot"
        is_repost = task_data.get('post_type') == 'repost'

        # LOGIC A: True Forwarding (Single Message)
        if is_repost and not media_group_json:
            sent_msg = await bot.forward_message(
                chat_id=channel_id,
                from_chat_id=content_chat_id,
                message_id=content_message_id,
                disable_notification=api_disable_notification
            )
            posted_message_id = sent_msg.message_id
            all_posted_ids = [posted_message_id]
            sent_msg_object = sent_msg

        # LOGIC B: From Bot (Copy) OR Album
        else:
            if media_group_json:
                media_data = media_group_json if isinstance(media_group_json, dict) else json.loads(media_group_json)

                # === REPOST MEDIA GROUP: Forward as group ===
                if is_repost:
                    # We rely on 'message_ids' being present in the saved JSON to forward the specific album messages
                    ids_to_forward = media_data.get('message_ids', [])

                    # Fallback: if no IDs stored, try to use the single content_message_id
                    if not ids_to_forward and content_message_id:
                        ids_to_forward = [content_message_id]

                    sent_msgs = []
                    
                    # Try forward_messages first (keeps "Forwarded from" header AND grouping)
                    if ids_to_forward:
                        try:
                            sent_msgs = await bot.forward_messages(
                                chat_id=channel_id,
                                from_chat_id=content_chat_id,
                                message_ids=ids_to_forward,
                                disable_notification=api_disable_notification
                            )
                        except Exception as e:
                            logger.warning(f"forward_messages failed: {e}, falling back to send_media_group...")
                    
                    # Fallback: Use send_media_group (preserves grouping, loses "Forwarded from" header)
                    if not sent_msgs and 'files' in media_data:
                        input_media = []
                        raw_caption = media_data.get('caption', '')
                        caption_to_use = raw_caption[:1024] if raw_caption else None
                        
                        for i, f in enumerate(media_data['files']):
                            media_obj = None
                            item_caption = caption_to_use if i == 0 else None
                            
                            if f['type'] == 'photo':
                                media_obj = InputMediaPhoto(media=f['media'], caption=item_caption,
                                                            has_spoiler=f.get('has_spoiler', False))
                            elif f['type'] == 'video':
                                media_obj = InputMediaVideo(media=f['media'], caption=item_caption,
                                                            has_spoiler=f.get('has_spoiler', False))
                            elif f['type'] == 'document':
                                media_obj = InputMediaDocument(media=f['media'], caption=item_caption)
                            elif f['type'] == 'audio':
                                media_obj = InputMediaAudio(media=f['media'], caption=item_caption)
                            
                            if media_obj:
                                input_media.append(media_obj)
                        
                        if input_media:
                            sent_msgs = await bot.send_media_group(
                                chat_id=channel_id,
                                media=input_media,
                                disable_notification=api_disable_notification
                            )

                    if sent_msgs:
                        all_posted_ids = [msg.message_id for msg in sent_msgs]
                        posted_message_id = sent_msgs[0].message_id
                        sent_msg_object = sent_msgs[0]
                    else:
                        raise Exception("No messages could be forwarded or reconstructed for this album.")

                # === FROM_BOT MEDIA GROUP: Reconstruct from file IDs ===
                elif 'files' in media_data:
                    input_media = []
                    raw_caption = media_data.get('caption', '')
                    caption_to_use = raw_caption[:1024] if raw_caption else None

                    for i, f in enumerate(media_data['files']):
                        media_obj = None
                        item_caption = caption_to_use if i == 0 else None

                        if f['type'] == 'photo':
                            media_obj = InputMediaPhoto(media=f['media'], caption=item_caption)
                        elif f['type'] == 'video':
                            media_obj = InputMediaVideo(media=f['media'], caption=item_caption)
                        elif f['type'] == 'document':
                            media_obj = InputMediaDocument(media=f['media'], caption=item_caption)
                        elif f['type'] == 'audio':
                            media_obj = InputMediaAudio(media=f['media'], caption=item_caption)

                        if media_obj:
                            input_media.append(media_obj)

                    if input_media:
                        sent_msgs = await bot.send_media_group(
                            chat_id=channel_id,
                            media=input_media,
                            disable_notification=api_disable_notification
                        )
                        all_posted_ids = [msg.message_id for msg in sent_msgs]
                        posted_message_id = sent_msgs[0].message_id
                        sent_msg_object = sent_msgs[0]
                    else:
                        raise Exception("Empty media group or invalid file types")

            # LOGIC C: Single Message Copy (From Bot)
            else:
                sent_msg = await bot.copy_message(
                    chat_id=channel_id,
                    from_chat_id=content_chat_id,
                    message_id=content_message_id,
                    disable_notification=api_disable_notification
                )
                posted_message_id = sent_msg.message_id
                all_posted_ids = [posted_message_id]
                sent_msg_object = sent_msg

        logger.info(f"✅ Published successfully. Main Msg ID: {posted_message_id}, Total Msgs: {len(all_posted_ids)}")

        # --- SIGNATURE LOGIC ---
        if not is_repost:  # Signatures cannot be applied to Forwards
            user_settings = get_user_settings(task_data['user_id'])
            if user_settings.get('tariff') == 'free':
                sig_row = db_query("SELECT signature FROM bot_settings WHERE id = 1", fetchone=True)
                if sig_row and sig_row.get('signature'):
                    signature = f"\n\n{sig_row['signature']}"
                    try:
                        # For media groups, apply signature to first message caption
                        if media_group_json and all_posted_ids:
                            media_data = media_group_json if isinstance(media_group_json, dict) else json.loads(media_group_json)
                            original_caption = media_data.get('caption', '') or ''
                            new_caption = (original_caption + signature)[:1024]
                            try:
                                await bot.edit_message_caption(
                                    chat_id=channel_id,
                                    message_id=posted_message_id,
                                    caption=new_caption,
                                    parse_mode=ParseMode.HTML
                                )
                                logger.info(f"✅ Signature applied to media group caption for job {job_id}")
                            except Exception as e:
                                logger.warning(f"Could not apply signature to media group: {e}")
                        
                        # For single messages from copy_message
                        elif isinstance(sent_msg_object, Message):
                            # sent_msg_object is a real Message (from forward or send_media_group)
                            if sent_msg_object.text:
                                new_text = (sent_msg_object.text + signature)[:4096]
                                await bot.edit_message_text(
                                    chat_id=channel_id,
                                    message_id=posted_message_id,
                                    text=new_text,
                                    parse_mode=ParseMode.HTML,
                                    disable_web_page_preview=True
                                )
                                logger.info(f"✅ Signature applied to text message for job {job_id}")
                            elif sent_msg_object.caption is not None:
                                current_caption = sent_msg_object.caption or ""
                                new_caption = (current_caption + signature)[:1024]
                                await bot.edit_message_caption(
                                    chat_id=channel_id,
                                    message_id=posted_message_id,
                                    caption=new_caption,
                                    parse_mode=ParseMode.HTML,
                                )
                                logger.info(f"✅ Signature applied to caption for job {job_id}")
                        else:
                            # copy_message returns MessageId, not Message
                            # We need to fetch the original content to determine what to edit
                            # Try to get original message from the bot's chat (source)
                            try:
                                # First try to edit as caption (for media messages)
                                await bot.edit_message_caption(
                                    chat_id=channel_id,
                                    message_id=posted_message_id,
                                    caption=signature.strip(),
                                    parse_mode=ParseMode.HTML,
                                )
                                logger.info(f"✅ Signature applied as caption for copied message job {job_id}")
                            except Exception:
                                # If that fails, try to get the message via getUpdates and edit text
                                try:
                                    # Try to edit as text (for text-only messages)
                                    # We need to get the current text first - use getChat/getUpdates workaround
                                    updates = await bot.get_updates(timeout=1)
                                    for update in updates:
                                        if update.channel_post and update.channel_post.message_id == posted_message_id:
                                            msg = update.channel_post
                                            if msg.text:
                                                new_text = (msg.text + signature)[:4096]
                                                await bot.edit_message_text(
                                                    chat_id=channel_id,
                                                    message_id=posted_message_id,
                                                    text=new_text,
                                                    parse_mode=ParseMode.HTML,
                                                    disable_web_page_preview=True
                                                )
                                                logger.info(f"✅ Signature applied via getUpdates for job {job_id}")
                                            elif msg.caption is not None:
                                                new_caption = ((msg.caption or "") + signature)[:1024]
                                                await bot.edit_message_caption(
                                                    chat_id=channel_id,
                                                    message_id=posted_message_id,
                                                    caption=new_caption,
                                                    parse_mode=ParseMode.HTML,
                                                )
                                                logger.info(f"✅ Signature applied to caption via getUpdates for job {job_id}")
                                            break
                                except Exception as e2:
                                    logger.warning(f"Signature fallback failed: {e2}")
                    except Exception as e:
                        logger.warning(f"⚠️ Could not apply signature to job {job_id}: {e}")

        # 4. PINNING
        pin_duration = float(job_data['pin_duration'] or 0)
        if pin_duration > 0 and posted_message_id:
            try:
                await bot.pin_chat_message(chat_id=channel_id, message_id=posted_message_id,
                                           disable_notification=api_disable_notification)
                unpin_time = datetime.now(ZoneInfo('UTC')) + timedelta(hours=pin_duration)
                context.application.job_queue.run_once(execute_unpin_job, when=unpin_time,
                                                       data={'channel_id': channel_id, 'message_id': posted_message_id,
                                                             'job_id': job_id},
                                                       name=f"unpin_{job_id}_{posted_message_id}")
            except Exception as e:
                logger.error(f"Pinning failed: {e}")

        # 5. AUTO DELETE - Pass all message IDs for media groups
        delete_hours = float(job_data['auto_delete_hours'] or 0)
        if delete_hours > 0 and posted_message_id:
            del_time = datetime.now(ZoneInfo('UTC')) + timedelta(hours=delete_hours)
            context.application.job_queue.run_once(execute_delete_job, when=del_time,
                                                   data={'channel_id': channel_id, 'message_id': posted_message_id,
                                                         'message_ids': all_posted_ids,  # Pass ALL message IDs
                                                         'job_id': job_id}, name=f"del_{job_id}_{posted_message_id}")

        # 6. UPDATE STATUS
        ids_json = json.dumps(all_posted_ids)
        db_query(
            "UPDATE publication_jobs SET status = 'published', published_at = NOW(), posted_message_id = %s, posted_message_ids = %s WHERE id = %s",
            (posted_message_id, ids_json, job_id), commit=True)

        # --- 7. REPORTING (Consolidated with Hyperlinks) ---
        # A. Fetch Channel Info
        ch_info = db_query("SELECT channel_username, channel_title FROM channels WHERE channel_id = %s", (channel_id,),
                           fetchone=True)
        raw_title = ch_info.get('channel_title', str(channel_id)) if ch_info else str(channel_id)
        channel_username = ch_info.get('channel_username') if ch_info else None

        # B. Generate Hyperlink
        if channel_username:
            post_link = f"https://t.me/{channel_username}/{posted_message_id}"
        else:
            clean_id = str(channel_id).replace("-100", "")
            post_link = f"https://t.me/c/{clean_id}/{posted_message_id}"

        # C. Format Entry
        safe_title = raw_title.replace("[", "").replace("]", "")
        formatted_link_entry = f"[{safe_title}]({post_link})"

        # D. Buffer for Consolidated Report
        batch_id = int(job_data['scheduled_time_utc'].timestamp())
        report_key = f"rep_{task_data['id']}_{batch_id}"

        if report_key not in context.bot_data:
            context.bot_data[report_key] = {
                'channels': [],
                'task_name': task_data.get('task_name'),
                'time': datetime.now(ZoneInfo('UTC')),
                'advertiser_id': job_data['advertiser_user_id'],
                'creator_id': task_data['user_id'],
                'report_enabled': bool(task_data.get('report_enabled', False))
            }
        context.bot_data[report_key]['channels'].append(formatted_link_entry)

        # E. Schedule Debounced Sender
        sender_job_name = f"send_{report_key}"
        existing_jobs = context.job_queue.get_jobs_by_name(sender_job_name)
        for job in existing_jobs:
            job.schedule_removal()

        context.job_queue.run_once(
            send_consolidated_report,
            when=3,
            data={'report_key': report_key},
            name=sender_job_name
        )

        # 8. SCHEDULE NEXT RECURRENCE
        schedules = get_task_schedules(task_data['id'])
        this_run_time_utc = job_data['scheduled_time_utc'].replace(tzinfo=ZoneInfo('UTC'))
        for schedule in schedules:
            if schedule['schedule_weekday'] is not None:
                next_run_utc = this_run_time_utc + timedelta(days=7)
                create_single_publication_job(task_data, channel_id, next_run_utc, context.application)

    except Exception as e:
        logger.error(f"❌ Execution failed for job {job_id}: {e}", exc_info=True)
        db_query("UPDATE publication_jobs SET status = 'failed' WHERE id = %s", (job_id,), commit=True)

async def send_consolidated_report(context: ContextTypes.DEFAULT_TYPE):
    """
    Sends the buffered report with multiple channels grouped together.
    Converts publication time to the recipient's specific timezone.
    """
    job_data = context.job.data
    report_key = job_data['report_key']

    # Retrieve data from buffer
    report_data = context.bot_data.get(report_key)
    if not report_data:
        return

    # Clean up buffer immediately to prevent double sending
    del context.bot_data[report_key]

    channel_links = report_data['channels']
    task_name = report_data['task_name']

    # Get the raw UTC time object
    raw_time_utc = report_data['time']

    # 2. Prepare users to notify (Advertiser + Creator)
    targets = []
    if report_data.get('advertiser_id'):
        targets.append(report_data['advertiser_id'])

    if report_data.get('creator_id') and report_data.get('report_enabled'):
        # Avoid duplicate if creator is the advertiser
        if report_data['creator_id'] != report_data.get('advertiser_id'):
            targets.append(report_data['creator_id'])

    # 3. Send messages
    for user_id in targets:
        try:
            # Get user settings for Language AND Timezone
            user_settings = get_user_settings(user_id)
            lang = user_settings.get('language_code', 'en')
            tz_name = user_settings.get('timezone', 'Europe/Moscow')

            # --- TIMEZONE CONVERSION ---
            try:
                user_tz = ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, ValueError):
                user_tz = ZoneInfo('UTC')

            # Handle both datetime objects (New Logic) and strings (Legacy/Fallback)
            if isinstance(raw_time_utc, datetime):
                # Convert UTC -> User TZ
                local_dt = raw_time_utc.astimezone(user_tz)
                time_str = local_dt.strftime('%Y-%m-%d %H:%M:%S')
            else:
                # Fallback if stored as string
                time_str = str(raw_time_utc)
            # ---------------------------

            # --- PREFIX LOCALIZATION ---
            try:
                prefix_template = get_text('notify_post_published_channel', context, lang=lang)
            except Exception:
                prefix_template = "📢 Канал: "

            separator_prefix = prefix_template.strip() + " "
            separator = f"\n{separator_prefix}"

            # Format the channel list
            channels_block = separator.join(channel_links)

            report_text = get_text('advertiser_report_template', context, lang=lang).format(
                channel_title=channels_block,
                task_title=task_name,
                time=time_str  # Now localized
            )

            await context.bot.send_message(
                chat_id=user_id,
                text=report_text,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Failed to send consolidated report to {user_id}: {e}")