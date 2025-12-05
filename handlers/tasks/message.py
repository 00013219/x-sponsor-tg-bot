import asyncio
import json

from telegram import Update, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo, InputMediaDocument, \
    InputMediaAudio, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from database.connection import db_query
from database.queries.tasks import get_task_details
from handlers.tasks.constructor import show_task_constructor
from keyboards.task_constructor import back_to_constructor_keyboard
from localization.loader import get_text
from services.task_service import update_task_field, get_or_create_task_id
from states.conversation import TASK_SET_MESSAGE, TASK_CONSTRUCTOR
from utils.cleanup import cleanup_temp_messages
from utils.logging import logger

# Character limits
MAX_SIMPLE_MESSAGE_LENGTH = 4096
MAX_MEDIA_CAPTION_LENGTH = 1024


async def task_ask_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажата кнопка '📝 Сообщение'"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    task = get_task_details(task_id)

    # Cleanup previous temp messages if any
    if query and query.message:
        await cleanup_temp_messages(context, query.message.chat_id)

    if task and task['content_message_id']:
        # --- EDIT MODE ---
        text = get_text('task_message_current_prompt', context)

        # 1. Delete the current message and send new ones instead of editing
        try:
            await query.delete_message()
        except Exception:
            pass

        # 2. Define Keyboard for the PREVIEW (Delete & Back)
        keyboard = [
            [InlineKeyboardButton(get_text('task_delete_message_btn', context), callback_data="task_delete_message")],
            [InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor")]
        ]

        # 3. Check if this is a repost (forward)
        is_repost = task.get('post_type') == 'repost'

        # 4. Check for Media Group (Album)
        media_group_json = task.get('media_group_data')

        if is_repost:
            # === REPOST MODE: Forward the original message(s) ===
            try:
                if media_group_json:
                    # For media group reposts, we need to forward concurrently to keep them grouped
                    media_data = media_group_json if isinstance(media_group_json, dict) else json.loads(
                        media_group_json)

                    if 'message_ids' in media_data:
                        # Create a list of forward tasks
                        tasks = [
                            context.bot.forward_message(
                                chat_id=query.message.chat_id,
                                from_chat_id=task['content_chat_id'],
                                message_id=msg_id
                            ) for msg_id in media_data['message_ids']
                        ]

                        # Execute all forwards AT THE SAME TIME
                        forwarded_msgs = await asyncio.gather(*tasks)

                        if 'temp_message_ids' not in context.user_data:
                            context.user_data['temp_message_ids'] = []

                        for msg in forwarded_msgs:
                            context.user_data['temp_message_ids'].append(msg.message_id)
                    else:
                        # Fallback: forward just the first message
                        forwarded = await context.bot.forward_message(
                            chat_id=query.message.chat_id,
                            from_chat_id=task['content_chat_id'],
                            message_id=task['content_message_id']
                        )
                        if 'temp_message_ids' not in context.user_data:
                            context.user_data['temp_message_ids'] = []
                        context.user_data['temp_message_ids'].append(forwarded.message_id)
                else:
                    # Single message repost
                    forwarded = await context.bot.forward_message(
                        chat_id=query.message.chat_id,
                        from_chat_id=task['content_chat_id'],
                        message_id=task['content_message_id']
                    )
                    if 'temp_message_ids' not in context.user_data:
                        context.user_data['temp_message_ids'] = []
                    context.user_data['temp_message_ids'].append(forwarded.message_id)

                # Send control message with buttons
                control_msg = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"{text}\n\n{get_text('choose_options', context)}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                if 'temp_message_ids' not in context.user_data:
                    context.user_data['temp_message_ids'] = []
                context.user_data['temp_message_ids'].append(control_msg.message_id)

            except Exception as e:
                logger.error(f"Failed to forward repost: {e}")
                error_msg = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"⚠️ Error: {e}"
                )
                if 'temp_message_ids' not in context.user_data:
                    context.user_data['temp_message_ids'] = []
                context.user_data['temp_message_ids'].append(error_msg.message_id)

        elif media_group_json:
            # === FROM_BOT MODE: Show reconstructed media group ===
            try:
                # Parse JSON if it's a string
                media_data = media_group_json if isinstance(media_group_json, dict) else json.loads(media_group_json)

                input_media = []
                raw_caption = media_data.get('caption', '')
                # Truncate caption to 1024 characters
                caption_to_use = raw_caption[:MAX_MEDIA_CAPTION_LENGTH] if raw_caption else None

                # Reconstruct InputMedia objects
                for i, f in enumerate(media_data['files']):
                    media_obj = None
                    # Assign caption only to the first item
                    current_caption = caption_to_use if i == 0 else None

                    if f['type'] == 'photo':
                        media_obj = InputMediaPhoto(media=f['media'], caption=current_caption,
                                                    has_spoiler=f.get('has_spoiler', False))
                    elif f['type'] == 'video':
                        media_obj = InputMediaVideo(media=f['media'], caption=current_caption,
                                                    has_spoiler=f.get('has_spoiler', False))
                    elif f['type'] == 'document':
                        media_obj = InputMediaDocument(media=f['media'], caption=current_caption)
                    elif f['type'] == 'audio':
                        media_obj = InputMediaAudio(media=f['media'], caption=current_caption)

                    if media_obj:
                        input_media.append(media_obj)

                # Send the album
                if input_media:
                    sent_messages = await context.bot.send_media_group(
                        chat_id=query.message.chat_id,
                        media=input_media
                    )

                    # Store ALL media group message IDs for cleanup
                    if 'temp_message_ids' not in context.user_data:
                        context.user_data['temp_message_ids'] = []

                    for msg in sent_messages:
                        context.user_data['temp_message_ids'].append(msg.message_id)

                # Send separate message for buttons (Albums can't have buttons)
                control_msg = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"{text}\n\n{get_text('choose_options', context)}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

                # Store control message ID for cleanup
                if 'temp_message_ids' not in context.user_data:
                    context.user_data['temp_message_ids'] = []
                context.user_data['temp_message_ids'].append(control_msg.message_id)

            except Exception as e:
                logger.error(f"Failed to preview media group: {e}")
                # Fallback: send error message that will be cleaned up later
                error_msg = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"{e}"
                )
                if 'temp_message_ids' not in context.user_data:
                    context.user_data['temp_message_ids'] = []
                context.user_data['temp_message_ids'].append(error_msg.message_id)

        else:
            # === SHOWING SINGLE MESSAGE (FROM_BOT) ===
            try:
                # Copy message (Preview) WITH buttons attached
                copied_message = await context.bot.copy_message(
                    chat_id=query.message.chat_id,
                    from_chat_id=task['content_chat_id'],
                    message_id=task['content_message_id'],
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                # Store single message ID for cleanup
                if 'temp_message_ids' not in context.user_data:
                    context.user_data['temp_message_ids'] = []
                context.user_data['temp_message_ids'].append(copied_message.message_id)

            except Exception as e:
                logger.warning(f"Не удалось скопировать старое сообщение для task {task_id}: {e}")
                error_msg = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=get_text('task_message_display_error', context)
                )
                if 'temp_message_ids' not in context.user_data:
                    context.user_data['temp_message_ids'] = []
                context.user_data['temp_message_ids'].append(error_msg.message_id)

        return TASK_SET_MESSAGE

    else:
        # --- ASK MODE ---
        text = get_text('task_ask_message', context)

        # Delete the current message and send a new one instead of editing
        try:
            await query.delete_message()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение: {e}")

        # Send new message and store its ID for cleanup
        msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=back_to_constructor_keyboard(context)
        )

        # Store this message ID for cleanup
        if 'temp_message_ids' not in context.user_data:
            context.user_data['temp_message_ids'] = []
        context.user_data['temp_message_ids'].append(msg.message_id)

        return TASK_SET_MESSAGE

async def task_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles receiving a message (or media group) for the task.
    Enhanced: Properly initializes temp_message_ids tracking.
    """
    user_id = update.message.from_user.id
    chat_id = update.effective_chat.id

    # Initialize temp_message_ids if not exists
    if 'temp_message_ids' not in context.user_data:
        context.user_data['temp_message_ids'] = []

    # Check if this message is part of a media group
    if update.message.media_group_id:
        media_group_id = update.message.media_group_id

        # Initialize buffer if not exists
        if 'media_group_buffer' not in context.user_data:
            context.user_data['media_group_buffer'] = {}

        if media_group_id not in context.user_data['media_group_buffer']:
            context.user_data['media_group_buffer'][media_group_id] = []

        # Add the current message object to the buffer
        context.user_data['media_group_buffer'][media_group_id].append(update.message)

        # Schedule the processing job (debounce)
        job_name = f"process_mg_{media_group_id}"
        existing_jobs = context.job_queue.get_jobs_by_name(job_name)

        if not existing_jobs:
            # Schedule execution in 2 seconds
            context.job_queue.run_once(
                process_media_group,
                when=2,
                data={'media_group_id': media_group_id},
                name=job_name,
                user_id=user_id,
                chat_id=chat_id
            )
        return TASK_SET_MESSAGE

    # --- Standard Single Message Logic ---
    return await save_single_task_message(update, context)

async def task_delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Конструктор) Удаление сохраненного сообщения"""
    query = update.callback_query
    await query.answer()

    task_id = context.user_data.get('current_task_id')
    if not task_id:
        await query.edit_message_text(get_text('error_generic', context))
        return await show_task_constructor(update, context)

    # Обнуляем данные в БД
    await update_task_field(task_id, 'content_message_id', None, context)
    await update_task_field(task_id, 'content_chat_id', None, context)
    db_query("UPDATE tasks SET message_snippet = NULL, media_group_data = NULL WHERE id = %s", (task_id,), commit=True)

    await query.answer(get_text('task_message_deleted_alert', context), show_alert=True)

    # TASK 2: Use edit (force_new_message=False)
    return await show_task_constructor(update, context, force_new_message=False)


async def save_single_task_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Helper to save a standard single message.
    Logics:
    1. Repost -> No Limit.
    2. Text -> 4096 chars.
    3. Media -> 1024 chars (API Limitation).
    """
    user_id = update.message.from_user.id
    task_id = get_or_create_task_id(user_id, context)

    if not task_id:
        await update.message.reply_text(get_text('error_generic', context))
        return TASK_CONSTRUCTOR

    message = update.message
    content_text = message.text or message.caption or ""

    # --- 🚀 DETECT POST TYPE FIRST (For Logic 1) ---
    # Check if the message is forwarded
    is_forward = (message.forward_date is not None) or \
                 (hasattr(message, 'forward_origin') and message.forward_origin is not None)

    new_post_type = 'repost' if is_forward else 'from_bot'

    # --- ✅ VALIDATION ---
    # Мы проверяем длину только если это НЕ репост (Logic 1: Repost without limits)
    if not is_forward:
        has_media = any([message.photo, message.video, message.document, message.audio, message.voice])

        # Logic 2: Text Only -> 4096 limit
        if not has_media and message.text:
            if len(message.text) > MAX_SIMPLE_MESSAGE_LENGTH:  # 4096
                error_msg = await update.message.reply_text(
                    get_text('error_message_too_long', context).format(
                        max_length=MAX_SIMPLE_MESSAGE_LENGTH,
                        current_length=len(message.text)
                    ),
                    parse_mode='HTML',
                    reply_markup=back_to_constructor_keyboard(context)
                )
                context.user_data['temp_message_ids'].append(error_msg.message_id)
                return TASK_SET_MESSAGE

        # Logic 3: Media + Text -> 1024 limit (Telegram API constraint for captions)
        if has_media and message.caption:
            if len(message.caption) > MAX_MEDIA_CAPTION_LENGTH:  # 1024
                error_msg = await update.message.reply_text(
                    get_text('error_caption_too_long', context).format(
                        max_length=MAX_MEDIA_CAPTION_LENGTH,
                        current_length=len(message.caption)
                    ),
                    parse_mode='HTML',
                    reply_markup=back_to_constructor_keyboard(context)
                )
                context.user_data['temp_message_ids'].append(error_msg.message_id)
                return TASK_SET_MESSAGE

    # ... [Existing Snippet Generation Code] ...
    if not content_text:
        if message.photo:
            content_text = "🖼 [Photo]"
        elif message.video:
            content_text = "📹 [Video]"
        elif message.document:
            content_text = "📄 [File]"
        elif message.audio:
            content_text = "🎵 [Audio]"
        elif message.voice:
            content_text = "🎤 [Voice]"
        elif message.sticker:
            content_text = "👾 [Sticker]"
        else:
            content_text = "📦 [Media]"

    # Generate snippet
    words = content_text.split()
    snippet = " ".join(words[:4]) + ("..." if len(words) > 4 else "")

    # Set Task Name if empty
    task = get_task_details(task_id)
    if not task.get('task_name'):
        new_name = snippet[:200] if snippet else "New Task"
        await update_task_field(task_id, 'task_name', new_name, context)

    # Save Post Type
    await update_task_field(task_id, 'post_type', new_post_type, context)

    # Save to DB
    content_message_id = message.message_id
    content_chat_id = message.chat_id

    await update_task_field(task_id, 'content_message_id', content_message_id, context)
    await update_task_field(task_id, 'content_chat_id', content_chat_id, context)

    # Directly update fields
    db_query("UPDATE tasks SET message_snippet = %s, media_group_data = NULL WHERE id = %s",
             (snippet, task_id), commit=True)

    # UI Feedback
    await send_task_preview(user_id, task_id, context, is_group=False)
    return TASK_SET_MESSAGE


async def process_media_group(context: ContextTypes.DEFAULT_TYPE):
    """
    Job that runs after a short delay to process a buffered media group.

    Logic:
    1. Collects all messages associated with the media_group_id.
    2. Detects if it is a Repost (Forward).
    3. If Repost -> Save message IDs for forwarding, no caption limit.
    4. If Fresh Post -> Validates caption <= 1024 chars, save file_ids.
    5. Saves to DB and triggers preview.
    """
    job = context.job
    job_data = job.data
    media_group_id = job_data['media_group_id']

    # User ID passed via job arguments
    user_id = job.user_id

    # Safety check
    if not context.user_data:
        logger.error(f"context.user_data is None for job {job.name}.")
        return

    # Initialize temp_message_ids if not exists (for error handling)
    if 'temp_message_ids' not in context.user_data:
        context.user_data['temp_message_ids'] = []

    # Retrieve messages from buffer
    buffer = context.user_data.get('media_group_buffer', {})
    messages = buffer.pop(media_group_id, [])

    # Save the cleaned buffer back to user_data
    if not buffer:
        context.user_data.pop('media_group_buffer', None)

    if not messages:
        logger.warning(f"No messages found for media group {media_group_id}")
        return

    # Sort messages by message_id to ensure correct order
    messages.sort(key=lambda m: m.message_id)

    task_id = get_or_create_task_id(user_id, context)

    # --- 🚀 DETECT POST TYPE FIRST ---
    # Check if the first message in the group is a forward
    first_msg = messages[0]
    is_forward = (first_msg.forward_date is not None) or \
                 (hasattr(first_msg, 'forward_origin') and first_msg.forward_origin is not None)

    new_post_type = 'repost' if is_forward else 'from_bot'

    # Extract Media Data & Caption
    caption = ""
    media_group_data = {}

    if is_forward:
        # === REPOST: Save message IDs for forwarding ===
        message_ids = [msg.message_id for msg in messages]

        # Capture caption (for snippet only, won't be used in forwarding)
        for msg in messages:
            if msg.caption and not caption:
                caption = msg.caption
                break

        media_group_data = {
            'caption': caption,  # Original caption, no truncation
            'message_ids': message_ids,
            'is_repost': True
        }

    else:
        # === FROM_BOT: Save file IDs for reconstruction ===
        media_list = []

        for msg in messages:
            # Capture caption from the first message that has one
            if msg.caption and not caption:
                caption = msg.caption

            file_id = None
            file_type = None

            if msg.photo:
                file_id = msg.photo[-1].file_id  # Best quality
                file_type = 'photo'
            elif msg.video:
                file_id = msg.video.file_id
                file_type = 'video'
            elif msg.document:
                file_id = msg.document.file_id
                file_type = 'document'
            elif msg.audio:
                file_id = msg.audio.file_id
                file_type = 'audio'

            if file_id:
                media_list.append({
                    'type': file_type,
                    'media': file_id,
                    'has_spoiler': msg.has_media_spoiler if hasattr(msg, 'has_media_spoiler') else False
                })

        # --- ✅ VALIDATION & TRUNCATION (only for from_bot) ---
        if caption and len(caption) > MAX_MEDIA_CAPTION_LENGTH:
            logger.warning(f"Caption too long ({len(caption)} chars), truncating to {MAX_MEDIA_CAPTION_LENGTH}")
            caption = caption[:MAX_MEDIA_CAPTION_LENGTH]

            # Optionally notify user
            warning_msg = await context.bot.send_message(
                chat_id=user_id,
                text=get_text('warning_caption_truncated', context).format(
                    max_length=MAX_MEDIA_CAPTION_LENGTH,
                    original_length=len(caption)
                ),
                parse_mode='HTML'
            )
            context.user_data['temp_message_ids'].append(warning_msg.message_id)

        media_group_data = {
            'caption': caption,
            'files': media_list,
            'is_repost': False
        }

    # Generate Snippet
    if caption:
        words = caption.split()
        short_caption = " ".join(words[:4])
        if len(words) > 4:
            short_caption += "..."
        snippet = f"📸 {short_caption}"
    else:
        snippet = "📸 Media Group"

    # Set Task Name if empty
    task = get_task_details(task_id)
    if not task.get('task_name'):
        new_name = snippet[:200]
        await update_task_field(task_id, 'task_name', new_name, context)

    # Save Post Type
    await update_task_field(task_id, 'post_type', new_post_type, context)

    # Save to DB
    # We save the ID of the first message in the group for reference
    first_msg_id = messages[0].message_id
    chat_id = messages[0].chat_id
    json_data = json.dumps(media_group_data)

    await update_task_field(task_id, 'content_message_id', first_msg_id, context)
    await update_task_field(task_id, 'content_chat_id', chat_id, context)

    db_query(
        "UPDATE tasks SET message_snippet = %s, media_group_data = %s WHERE id = %s",
        (snippet, json_data, task_id),
        commit=True
    )

    # Trigger UI update
    await send_task_preview(user_id, task_id, context, is_group=True, media_data=media_group_data)


async def send_task_preview(user_id, task_id, context, is_group=False, media_data=None):
    """Helper to send the saved confirmation and preview with proper tracking"""

    # Initialize temp_message_ids if not exists
    if 'temp_message_ids' not in context.user_data:
        context.user_data['temp_message_ids'] = []

    # Send PREVIEW
    if is_group and media_data:
        try:
            # Parse JSON if it's a string
            media_data = media_data if isinstance(media_data, dict) else json.loads(media_data)

            # Check if this is a repost
            is_repost = media_data.get('is_repost', False)

            if is_repost and 'message_ids' in media_data:
                # === REPOST: Forward all messages ===
                task = get_task_details(task_id)
                for msg_id in media_data['message_ids']:
                    try:
                        forwarded = await context.bot.forward_message(
                            chat_id=user_id,
                            from_chat_id=task['content_chat_id'],
                            message_id=msg_id
                        )
                        context.user_data['temp_message_ids'].append(forwarded.message_id)
                    except Exception as e:
                        logger.error(f"Failed to forward message {msg_id}: {e}")
            else:
                # === FROM_BOT: Reconstruct media group ===
                input_media = []
                raw_caption = media_data.get('caption', '')
                # Truncate caption before creating InputMedia objects
                caption_to_use = raw_caption[:MAX_MEDIA_CAPTION_LENGTH] if raw_caption else None

                for i, f in enumerate(media_data.get('files', [])):
                    media_obj = None
                    # Determine InputMedia class based on file type
                    media_class = None
                    if f['type'] == 'photo':
                        media_class = InputMediaPhoto
                    elif f['type'] == 'video':
                        media_class = InputMediaVideo
                    elif f['type'] == 'document':
                        media_class = InputMediaDocument
                    elif f['type'] == 'audio':
                        media_class = InputMediaAudio

                    if media_class:
                        kwargs = {'media': f['media']}

                        # Only the first item gets the caption
                        if i == 0 and caption_to_use:
                            kwargs['caption'] = caption_to_use

                        # Photos and Videos support has_spoiler
                        if media_class in (InputMediaPhoto, InputMediaVideo):
                            kwargs['has_spoiler'] = f.get('has_spoiler', False)

                        media_obj = media_class(**kwargs)
                        input_media.append(media_obj)

                if input_media:
                    logger.info(f"Sending media group with {len(input_media)} items")
                    try:
                        sent_msgs = await context.bot.send_media_group(chat_id=user_id, media=input_media)
                        logger.info(f"Successfully sent {len(sent_msgs)} messages in media group")
                        # Store ALL media group message IDs for cleanup
                        for msg in sent_msgs:
                            context.user_data['temp_message_ids'].append(msg.message_id)
                    except TelegramError as te:
                        # Fallback to copying the original first message
                        logger.warning(f"send_media_group failed: {te}. Falling back to copy_message.")
                        task = get_task_details(task_id)
                        if task and task['content_message_id']:
                            fallback_msg = await context.bot.copy_message(
                                chat_id=user_id,
                                from_chat_id=task['content_chat_id'],
                                message_id=task['content_message_id']
                            )
                            context.user_data['temp_message_ids'].append(fallback_msg.message_id)
                else:
                    error_msg = await context.bot.send_message(chat_id=user_id,
                                                               text="⚠️ Error: Could not compile media group for preview.")
                    context.user_data['temp_message_ids'].append(error_msg.message_id)

        except Exception as e:
            logger.error(f"Group preview failed: {e}", exc_info=True)
            error_msg = await context.bot.send_message(chat_id=user_id,
                                                       text="⚠️ Critical error generating group preview.")
            context.user_data['temp_message_ids'].append(error_msg.message_id)
    else:
        # Standard Single Message Preview
        task = get_task_details(task_id)

        # Check if it's a repost
        is_repost = task.get('post_type') == 'repost'

        try:
            if is_repost:
                # Forward the message to preserve repost status
                preview_msg = await context.bot.forward_message(
                    chat_id=user_id,
                    from_chat_id=task['content_chat_id'],
                    message_id=task['content_message_id']
                )
            else:
                # Copy the message (removes forward information)
                preview_msg = await context.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=task['content_chat_id'],
                    message_id=task['content_message_id']
                )
            context.user_data['temp_message_ids'].append(preview_msg.message_id)
        except Exception as e:
            logger.error(f"Preview failed: {e}")
            error_msg = await context.bot.send_message(chat_id=user_id, text="⚠️ Error displaying message preview.")
            context.user_data['temp_message_ids'].append(error_msg.message_id)

    success_text = get_text('task_message_saved', context)
    footer_text = get_text('task_message_preview_footer', context)
    keyboard = [
        [InlineKeyboardButton(get_text('task_delete_message_btn', context), callback_data="task_delete_message")],
        [InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor")]
    ]

    # Send confirmation message and track it
    confirmation_msg = await context.bot.send_message(
        chat_id=user_id,
        text=f"{success_text}\n\n{footer_text}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    # Track the confirmation message as well
    context.user_data['temp_message_ids'].append(confirmation_msg.message_id)