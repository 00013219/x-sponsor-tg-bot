from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import OWNER_ID
from database.connection import db_query
from localization.loader import get_text
from states.conversation import BOSS_PANEL


async def boss_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика бота"""
    query = update.callback_query
    await query.answer(get_text('boss_stats_loading', context))

    stats = get_bot_statistics()

    text = get_text('boss_stats_title', context) + "\n\n"
    text += get_text('boss_stats_total_users', context).format(total_users=stats['total_users']) + "\n"
    text += get_text('boss_stats_active_users', context).format(active_users=stats['active_users']) + "\n"
    text += get_text('boss_stats_tasks_today', context).format(tasks_today=stats['tasks_today']) + "\n"
    text += get_text('boss_stats_tasks_active', context).format(tasks_active=stats['tasks_active']) + "\n"
    text += get_text('boss_stats_tasks_completed', context).format(tasks_completed=stats['tasks_completed']) + "\n"
    text += get_text('boss_stats_tasks_total', context).format(tasks_total=stats['tasks_total']) + "\n\n"
    text += get_text('boss_stats_users_30d', context).format(users_30d=stats['users_30d']) + "\n"
    text += get_text('boss_stats_users_60d', context).format(users_60d=stats['users_60d']) + "\n\n"
    text += get_text('boss_stats_db_size', context).format(db_size=stats['db_size'])

    if stats['db_size'] and 'MB' in stats['db_size']:
        try:
            size_mb = float(stats['db_size'].split()[0])
            if size_mb > 100:
                text += get_text('boss_stats_db_warning', context)
        except:
            pass

    keyboard = [[InlineKeyboardButton(get_text('boss_stats_refresh', context), callback_data="boss_stats")],
                [InlineKeyboardButton(get_text('boss_back_btn', context), callback_data="nav_boss")]]

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BOSS_PANEL

def get_bot_statistics():
    """Get bot statistics for admin panel"""
    stats = {}

    # Total users
    result = db_query("SELECT COUNT(*) as count FROM users WHERE is_active = TRUE", fetchone=True)
    stats['total_users'] = result['count'] if result else 0

    # Active users (used bot in last 30 days)
    result = db_query("""
        SELECT COUNT(DISTINCT user_id) as count 
        FROM tasks 
        WHERE created_at > NOW() - INTERVAL '30 days'
    """, fetchone=True)
    stats['active_users'] = result['count'] if result else 0

    # Tasks created today
    result = db_query("""
        SELECT COUNT(*) as count 
        FROM tasks 
        WHERE DATE(created_at) = CURRENT_DATE
    """, fetchone=True)
    stats['tasks_today'] = result['count'] if result else 0

    # Active tasks
    result = db_query("SELECT COUNT(*) as count FROM tasks WHERE status = 'active'", fetchone=True)
    stats['tasks_active'] = result['count'] if result else 0

    # Completed tasks
    result = db_query("SELECT COUNT(*) as count FROM publication_jobs WHERE status = 'published'", fetchone=True)
    stats['tasks_completed'] = result['count'] if result else 0

    # Total tasks in DB
    result = db_query("SELECT COUNT(*) as count FROM tasks", fetchone=True)
    stats['tasks_total'] = result['count'] if result else 0

    # Database size
    result = db_query("""
        SELECT pg_size_pretty(pg_database_size(current_database())) as size
    """, fetchone=True)
    stats['db_size'] = result['size'] if result else 'N/A'

    # User growth (last 30 days)
    result = db_query("""
        SELECT COUNT(*) as count 
        FROM users 
        WHERE created_at > NOW() - INTERVAL '30 days'
    """, fetchone=True)
    stats['users_30d'] = result['count'] if result else 0

    # User growth (last 60 days)
    result = db_query("""
        SELECT COUNT(*) as count 
        FROM users 
        WHERE created_at > NOW() - INTERVAL '60 days'
    """, fetchone=True)
    stats['users_60d'] = result['count'] if result else 0

    return stats

async def debug_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command to check scheduled jobs - add as command handler"""
    if update.effective_user.id != OWNER_ID:
        return

    # Check scheduler jobs
    # ***** MODIFIED HERE *****
    jobs = context.application.job_queue.get_jobs()
    text = f"📊 Scheduler jobs (job_queue): {len(jobs)}\n\n"

    for job in jobs[:10]:  # Show first 10
        text += f"ID: {job.id}\n"
        text += f"Name: {job.name}\n"
        text += f"Next run: {job.next_run_time}\n\n"

    # Check DB jobs
    db_jobs = db_query(
        "SELECT COUNT(*) as count, status FROM publication_jobs GROUP BY status",
        fetchall=True
    )

    text += "\n📚 DB Jobs:\n"
    if db_jobs:
        for row in db_jobs:
            text += f"{row['status']}: {row['count']}\n"
    else:
        text += "No jobs in DB."

    await update.message.reply_text(text)
