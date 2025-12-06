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
    """
    Shows preview of the task.
    Handles 'Forwarded' header correctly for Album Reposts by forwarding individually.
    """
    query = update.callback_query
    await query.answer()
    task_id = context.user_data.get('current_task_id')
    task = get_task_details(task_id)

    # Cleanup previous temp messages if any
    if query and query.message:
        await cleanup_temp_messages(context, query.message.chat_id)

    if task and task['content_message_id']:
        # --- EDIT MODE (Showing Preview) ---
        text = get_text('task_message_current_prompt', context)

        # 1. Delete the previous prompt
        try:
            await query.delete_message()
        except Exception:
            pass

        # 2. Define Keyboard
        keyboard = [
            [InlineKeyboardButton(get_text('task_delete_message_btn', context), callback_data="task_delete_message")],
            [InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor")]
        ]

        # 3. Determine Type
        is_repost = task.get('post_type') == 'repost'
        media_group_json = task.get('media_group_data')

        if is_repost:
            # === REPOST MODE (Forwarding) ===
            try:
                # Initialize list to track preview messages
                if 'temp_message_ids' not in context.user_data:
                    context.user_data['temp_message_ids'] = []

                if media_group_json:
                    # FORWARD MEDIA GROUP
                    media_data = media_group_json if isinstance(media_group_json, dict) else json.loads(
                        media_group_json)

                    ids_to_forward = media_data.get('message_ids', [])
                    # Fallback if list is empty, use main ID
                    if not ids_to_forward:
                        ids_to_forward = [task['content_message_id']]

                    # Try forward_messages first (keeps "Forwarded from" header AND grouping)
                    forwarded_msgs = []
                    try:
                        forwarded_msgs = await context.bot.forward_messages(
                            chat_id=query.message.chat_id,
                            from_chat_id=task['content_chat_id'],
                            message_ids=ids_to_forward
                        )
                        if forwarded_msgs:
                            for fwd in forwarded_msgs:
                                context.user_data['temp_message_ids'].append(fwd.message_id)
                    except Exception as e:
                        logger.warning(f"forward_messages failed: {e}, falling back to send_media_group...")
                    
                    # Fallback: Use send_media_group (preserves grouping, loses "Forwarded from" header)
                    if not forwarded_msgs and 'files' in media_data:
                        input_media = []
                        caption_to_use = media_data.get('caption', '')[:1024] if media_data.get('caption') else None
                        
                        for i, f in enumerate(media_data['files']):
                            media_obj = None
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
                        
                        if input_media:
                            sent_msgs = await context.bot.send_media_group(
                                chat_id=query.message.chat_id,
                                media=input_media
                            )
                            for msg in sent_msgs:
                                context.user_data['temp_message_ids'].append(msg.message_id)
                            forwarded_msgs = sent_msgs  # Mark as successful
                    
                    # If still no messages, raise an error
                    if not forwarded_msgs:
                        raise Exception("Could not forward or reconstruct the media group")
                else:
                    # FORWARD SINGLE MESSAGE
                    forwarded = await context.bot.forward_message(
                        chat_id=query.message.chat_id,
                        from_chat_id=task['content_chat_id'],
                        message_id=task['content_message_id']
                    )
                    context.user_data['temp_message_ids'].append(forwarded.message_id)

                # Send separate control message
                control_msg = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"{text}\n\n{get_text('choose_options', context)}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                context.user_data['temp_message_ids'].append(control_msg.message_id)

            except Exception as e:
                logger.error(f"Failed to forward repost preview: {e}")
                error_msg = await context.bot.send_message(chat_id=query.message.chat_id, text=f"⚠️ Error: {e}")
                context.user_data.setdefault('temp_message_ids', []).append(error_msg.message_id)

        elif media_group_json:
            # === FROM_BOT MODE (Reconstruct Album) ===
            try:
                media_data = media_group_json if isinstance(media_group_json, dict) else json.loads(media_group_json)
                input_media = []
                raw_caption = media_data.get('caption', '')
                # Truncate caption
                caption_to_use = raw_caption[:1024] if raw_caption else None

                for i, f in enumerate(media_data['files']):
                    media_obj = None
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

                if input_media:
                    sent_messages = await context.bot.send_media_group(
                        chat_id=query.message.chat_id,
                        media=input_media
                    )
                    if 'temp_message_ids' not in context.user_data:
                        context.user_data['temp_message_ids'] = []
                    for msg in sent_messages:
                        context.user_data['temp_message_ids'].append(msg.message_id)

                # Send separate control buttons (Albums can't have buttons)
                control_msg = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"{text}\n\n{get_text('choose_options', context)}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                context.user_data.setdefault('temp_message_ids', []).append(control_msg.message_id)

            except Exception as e:
                logger.error(f"Failed to preview media group: {e}")
                error_msg = await context.bot.send_message(chat_id=query.message.chat_id, text=f"{e}")
                context.user_data.setdefault('temp_message_ids', []).append(error_msg.message_id)

        else:
            # === FROM_BOT SINGLE MESSAGE ===
            try:
                # Copy message (Preview) WITH buttons attached
                copied_message = await context.bot.copy_message(
                    chat_id=query.message.chat_id,
                    from_chat_id=task['content_chat_id'],
                    message_id=task['content_message_id'],
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                if 'temp_message_ids' not in context.user_data:
                    context.user_data['temp_message_ids'] = []
                context.user_data['temp_message_ids'].append(copied_message.message_id)
            except Exception as e:
                error_str = str(e).lower()
                # If caption too long, fallback to forward + separate buttons for preview
                if 'caption' in error_str and 'long' in error_str:
                    logger.warning(f"copy_message failed due to long caption, using forward for preview")
                    try:
                        # Forward the message (shows "Forwarded from" but at least works)
                        forwarded_msg = await context.bot.forward_message(
                            chat_id=query.message.chat_id,
                            from_chat_id=task['content_chat_id'],
                            message_id=task['content_message_id']
                        )
                        if 'temp_message_ids' not in context.user_data:
                            context.user_data['temp_message_ids'] = []
                        context.user_data['temp_message_ids'].append(forwarded_msg.message_id)
                        
                        # Send separate control message
                        control_msg = await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=get_text('task_message_current_prompt', context),
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        context.user_data['temp_message_ids'].append(control_msg.message_id)
                    except Exception as e2:
                        logger.error(f"Forward fallback also failed for task {task_id}: {e2}")
                        error_msg = await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=get_text('task_message_display_error', context)
                        )
                        context.user_data.setdefault('temp_message_ids', []).append(error_msg.message_id)
                else:
                    logger.warning(f"Failed to copy old message for task {task_id}: {e}")
                    error_msg = await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=get_text('task_message_display_error', context)
                    )
                    context.user_data.setdefault('temp_message_ids', []).append(error_msg.message_id)

        return TASK_SET_MESSAGE
    else:
        # --- ASK MODE (No message set yet) ---
        text = get_text('task_ask_message', context)
        try:
            await query.delete_message()
        except Exception as e:
            logger.warning(f"Failed to delete message: {e}")

        msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=back_to_constructor_keyboard(context)
        )
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
    # Check if the message is forwarded (using forward_origin for python-telegram-bot v20+)
    is_forward = message.forward_origin is not None

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
    FIXED: Properly saves all media in the group for reconstruction as a single media group.
    """
    job = context.job
    job_data = job.data
    media_group_id = job_data['media_group_id']
    user_id = job.user_id

    if not context.user_data:
        logger.error(f"context.user_data is None for job {job.name}.")
        return

    if 'temp_message_ids' not in context.user_data:
        context.user_data['temp_message_ids'] = []

    # Retrieve messages from buffer
    buffer = context.user_data.get('media_group_buffer', {})
    messages = buffer.pop(media_group_id, [])

    if not buffer:
        context.user_data.pop('media_group_buffer', None)

    if not messages:
        logger.warning(f"No messages found for media group {media_group_id}")
        return

    # Sort messages by message_id to ensure correct order
    messages.sort(key=lambda m: m.message_id)

    task_id = get_or_create_task_id(user_id, context)

    # --- DETECT POST TYPE ---
    first_msg = messages[0]
    # Check if the message is forwarded (using forward_origin for python-telegram-bot v20+)
    is_forward = first_msg.forward_origin is not None

    new_post_type = 'repost' if is_forward else 'from_bot'

    # Extract Media Data & Caption
    caption = ""
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

    # For from_bot posts, validate caption length
    if not is_forward and caption and len(caption) > MAX_MEDIA_CAPTION_LENGTH:
        original_length = len(caption)
        caption = caption[:MAX_MEDIA_CAPTION_LENGTH]
        logger.warning(f"Caption too long ({original_length} chars), truncating to {MAX_MEDIA_CAPTION_LENGTH}")

        warning_msg = await context.bot.send_message(
            chat_id=user_id,
            text=get_text('warning_caption_truncated', context).format(
                max_length=MAX_MEDIA_CAPTION_LENGTH,
                original_length=original_length
            ),
            parse_mode='HTML'
        )
        context.user_data['temp_message_ids'].append(warning_msg.message_id)

    # ✅ Build complete media_group_data with ALL files
    media_group_data = {
        'caption': caption,
        'files': media_list,  # All media files in the group
        'is_repost': is_forward,
        'message_ids': [m.message_id for m in messages]
    }

    # Generate Snippet
    if caption:
        words = caption.split()
        short_caption = " ".join(words[:4])
        if len(words) > 4:
            short_caption += "..."
        snippet = f"📸 {short_caption}"
    else:
        snippet = f"📸 Media Group ({len(media_list)} items)"

    # Set Task Name if empty
    task = get_task_details(task_id)
    if not task.get('task_name'):
        new_name = snippet[:200]
        await update_task_field(task_id, 'task_name', new_name, context)

    # Save Post Type
    await update_task_field(task_id, 'post_type', new_post_type, context)

    # Save to DB
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
    """
    Helper to send the saved confirmation and preview with proper tracking.
    ADJUSTED: Now supports 'Forwarded' header for Media Groups by using forward_message.
    """
    # Initialize temp_message_ids if not exists
    if 'temp_message_ids' not in context.user_data:
        context.user_data['temp_message_ids'] = []

    # Fetch task details to check post_type
    task = get_task_details(task_id)
    is_repost = task.get('post_type') == 'repost' if task else False

    # --- PREVIEW LOGIC ---
    if is_group and media_data:
        try:
            # Parse JSON if it's a string
            media_data = media_data if isinstance(media_data, dict) else json.loads(media_data)

            # === A. REPOST (FORWARD) MODE ===
            # We must forward messages individually to keep the "Forwarded from" header
            if is_repost:
                # We need message_ids to forward.
                # If they are in media_data (saved task), use them.
                # If not (fresh preview), try to use task content_message_id as fallback.
                ids_to_forward = media_data.get('message_ids', [])

                if not ids_to_forward and task.get('content_message_id'):
                    ids_to_forward = [task['content_message_id']]

                fwds = []
                
                # Try forward_messages first (keeps "Forwarded from" header AND grouping)
                if ids_to_forward:
                    try:
                        fwds = await context.bot.forward_messages(
                            chat_id=user_id,
                            from_chat_id=task['content_chat_id'],
                            message_ids=ids_to_forward
                        )
                        if fwds:
                            for fwd in fwds:
                                context.user_data['temp_message_ids'].append(fwd.message_id)
                    except Exception as e:
                        logger.warning(f"Preview forward_messages failed: {e}, falling back to send_media_group...")
                
                # Fallback: Use send_media_group (preserves grouping, loses "Forwarded from" header)
                if not fwds and 'files' in media_data:
                    input_media = []
                    caption_to_use = media_data.get('caption', '')[:1024] if media_data.get('caption') else None
                    
                    for i, f in enumerate(media_data['files']):
                        media_obj = None
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
                    
                    if input_media:
                        sent_msgs = await context.bot.send_media_group(chat_id=user_id, media=input_media)
                        for msg in sent_msgs:
                            context.user_data['temp_message_ids'].append(msg.message_id)
                        fwds = sent_msgs  # Mark as successful
                
                if not fwds:
                    raise Exception("Could not forward or reconstruct the media group for preview")

            # === B. FROM BOT (COPY/UPLOAD) MODE ===
            # Reconstruct the album and send as new messages
            else:
                input_media = []
                caption_to_use = media_data.get('caption', '')

                for i, f in enumerate(media_data['files']):
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
                        if i == 0:
                            kwargs['caption'] = caption_to_use
                        # Photos/Videos support spoilers
                        if media_class in (InputMediaPhoto, InputMediaVideo):
                            kwargs['has_spoiler'] = f.get('has_spoiler', False)

                        input_media.append(media_class(**kwargs))

                if input_media:
                    sent_msgs = await context.bot.send_media_group(chat_id=user_id, media=input_media)
                    for msg in sent_msgs:
                        context.user_data['temp_message_ids'].append(msg.message_id)
                else:
                    raise Exception("Empty media group")

        except Exception as e:
            logger.error(f"Group preview failed: {e}")
            error_msg = await context.bot.send_message(chat_id=user_id, text="⚠️ Error displaying group preview.")
            context.user_data['temp_message_ids'].append(error_msg.message_id)

    else:
        # --- SINGLE MESSAGE PREVIEW ---
        try:
            if is_repost:
                # Forward for Repost
                preview_msg = await context.bot.forward_message(
                    chat_id=user_id,
                    from_chat_id=task['content_chat_id'],
                    message_id=task['content_message_id']
                )
            else:
                # Copy for From Bot
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

    # --- CONFIRMATION BUTTONS ---
    success_text = get_text('task_message_saved', context)
    footer_text = get_text('task_message_preview_footer', context)

    keyboard = [
        [InlineKeyboardButton(get_text('task_delete_message_btn', context), callback_data="task_delete_message")],
        [InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor")]
    ]

    confirmation_msg = await context.bot.send_message(
        chat_id=user_id,
        text=f"{success_text}\n\n{footer_text}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data['temp_message_ids'].append(confirmation_msg.message_id)

