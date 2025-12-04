from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database.connection import db_query
from database.queries.task_channels import get_task_channels
from database.queries.tasks import get_task_details
from localization.loader import get_text
from utils.time_utils import format_hours_to_dhms


def task_constructor_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Клавиатура конструктора (Dynamic Labels with Localization)"""
    task_id = context.user_data.get('current_task_id')
    task = get_task_details(task_id)

    # --- Defaults ---
    pin_val = 0
    delete_val = 0
    push_val = False
    report_val = False
    post_type = 'repost'
    is_active = False
    has_message = False
    has_channels = False

    if task:
        pin_val = task.get('pin_duration', 0)
        delete_val = task.get('auto_delete_hours', 0)
        push_val = task.get('pin_notify', False)
        report_val = task.get('report_enabled', False)
        post_type = task.get('post_type', 'repost')
        if task.get('status') == 'active':
            # Check if there are actually scheduled jobs
            future_jobs = db_query("""
                        SELECT COUNT(*) as count FROM publication_jobs 
                        WHERE task_id = %s AND status = 'scheduled'
                    """, (task_id,), fetchone=True)
            is_active = future_jobs and future_jobs['count'] > 0
        else:
            is_active = False
        has_message = bool(task.get('content_message_id'))
        channels = get_task_channels(task_id)
        has_channels = bool(channels)

    # Buttons
    lbl_msg = get_text('task_set_message_btn', context)
    val_msg = "✅" if has_message else "❌"

    lbl_ch = get_text('task_select_channels_btn', context)
    val_ch = "✅" if has_channels else "❌"

    lbl_pin = get_text('task_set_pin_btn', context)

    val_push = "✅" if push_val else "❌"

    # Pin Notify Button Label
    lbl_push = get_text('alert_pin_notify_status', context).format(status=val_push)

    lbl_delete = get_text('task_set_delete_btn', context)
    lbl_report = get_text('task_set_report_btn', context)
    val_report = "✅" if report_val else "❌"

    lbl_type = get_text('task_set_post_type_btn', context)
    val_type = "🤖" if post_type == 'from_bot' else "↪️"

    if is_active:
        action_btn = InlineKeyboardButton(get_text('task_btn_deactivate', context), callback_data="task_deactivate")
    else:
        action_btn = InlineKeyboardButton(get_text('task_activate_btn', context), callback_data="task_activate")

    # Helper for display - uses the global function now
    def get_display_time(val):
        if val <= 0:
            return get_text('duration_no', context)
        return format_hours_to_dhms(val, context)

    # --- Construct Keyboard ---
    keyboard = [
        [InlineKeyboardButton(get_text('task_set_name_btn', context), callback_data="task_set_name")],
        [InlineKeyboardButton(f"{lbl_msg} {val_msg}", callback_data="task_set_message")],
        [InlineKeyboardButton(f"{lbl_ch} {val_ch}", callback_data="task_select_channels")],
        [
            InlineKeyboardButton(get_text('task_select_calendar_btn', context), callback_data="task_select_calendar"),
            InlineKeyboardButton(get_text('task_select_time_btn', context), callback_data="task_select_time")
        ],
    ]

    # Pin Row
    pin_row = [InlineKeyboardButton(f"{lbl_pin}: {get_display_time(pin_val)}", callback_data="task_set_pin")]

    # TASK 7: Only add Notify button if Pin is ON
    if pin_val > 0:
        pin_row.append(InlineKeyboardButton(lbl_push, callback_data="task_set_pin_notify"))

    keyboard.append(pin_row)

    keyboard.append(
        [InlineKeyboardButton(f"{lbl_delete}: {get_display_time(delete_val)}", callback_data="task_set_delete")])
    keyboard.append([InlineKeyboardButton(f"{lbl_report}: {val_report}", callback_data="task_set_report")])
    keyboard.append(
        [InlineKeyboardButton(get_text('task_set_advertiser_btn', context), callback_data="task_set_advertiser")])
    keyboard.append([InlineKeyboardButton(f"{lbl_type}: {val_type}", callback_data="task_set_post_type")])
    keyboard.append([InlineKeyboardButton(get_text('task_delete_btn', context), callback_data="task_delete")])
    keyboard.append([action_btn])
    keyboard.append([
        InlineKeyboardButton(get_text('back_btn', context), callback_data="nav_my_tasks"),
        InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
    ])

    return InlineKeyboardMarkup(keyboard)

def back_to_constructor_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Кнопки 'Назад' и 'Главное меню' (согласно ТЗ)"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(get_text('back_btn', context), callback_data="task_back_to_constructor"),
            InlineKeyboardButton(get_text('home_main_menu_btn', context), callback_data="nav_main_menu")
        ]
    ])

def back_to_main_menu_keyboard(context: ContextTypes.DEFAULT_TYPE, prefix: str = "nav"):
    """Кнопка 'Назад' в Главное меню"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text('back_btn', context), callback_data=f"{prefix}_main_menu")]
    ])
