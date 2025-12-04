#!/usr/bin/env python3
"""
Daily database backup script.
Backs up PostgreSQL database and stores in timestamped files.
Can be run with docker-compose exec or as a standalone script.
"""

import os
import subprocess
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database configuration from environment
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_NAME = os.getenv('DB_NAME', 'xsb_db')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')

# Backup configuration
BACKUP_DIR = os.getenv('BACKUP_DIR', './backups')
RETENTION_DAYS = int(os.getenv('BACKUP_RETENTION_DAYS', '7'))
COMPRESSION = os.getenv('BACKUP_COMPRESSION', 'true').lower() == 'true'


def ensure_backup_dir():
    """Create backup directory if it doesn't exist"""
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    logger.info(f"Backup directory: {BACKUP_DIR}")


def generate_backup_filename():
    """Generate timestamped backup filename"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"xsb_db_backup_{timestamp}"
    
    if COMPRESSION:
        filename += ".sql.gz"
    else:
        filename += ".sql"
    
    return os.path.join(BACKUP_DIR, filename)


def create_backup():
    """Create database backup using pg_dump"""
    backup_file = generate_backup_filename()
    
    logger.info(f"Starting backup of database '{DB_NAME}'...")
    logger.info(f"Backup file: {backup_file}")
    
    # Set password via environment for pg_dump
    env = os.environ.copy()
    env['PGPASSWORD'] = DB_PASSWORD
    
    try:
        if COMPRESSION:
            # pg_dump with gzip compression
            cmd = [
                'pg_dump',
                '-h', DB_HOST,
                '-p', DB_PORT,
                '-U', DB_USER,
                '-d', DB_NAME,
                '--verbose',
                '-O',  # No owner commands
                '-x',  # No privileges
            ]
            
            with open(backup_file, 'wb') as f:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env
                )
                
                gzip_process = subprocess.Popen(
                    ['gzip'],
                    stdin=process.stdout,
                    stdout=f,
                    stderr=subprocess.PIPE,
                    env=env
                )
                
                process.stdout.close()
                stdout, stderr = gzip_process.communicate()
                
                if gzip_process.returncode != 0:
                    raise Exception(f"Compression failed: {stderr.decode()}")
        else:
            # pg_dump without compression
            cmd = [
                'pg_dump',
                '-h', DB_HOST,
                '-p', DB_PORT,
                '-U', DB_USER,
                '-d', DB_NAME,
                '--verbose',
                '-O',
                '-x',
                '-f', backup_file
            ]
            
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True
            )
            
            if result.returncode != 0:
                raise Exception(f"pg_dump failed: {result.stderr}")
        
        # Get file size
        file_size = os.path.getsize(backup_file)
        file_size_mb = file_size / (1024 * 1024)
        
        logger.info(f"✅ Backup completed successfully")
        logger.info(f"📦 File: {backup_file}")
        logger.info(f"📊 Size: {file_size_mb:.2f} MB")
        
        return backup_file
        
    except Exception as e:
        logger.error(f"❌ Backup failed: {e}")
        # Clean up incomplete backup file
        if os.path.exists(backup_file):
            os.remove(backup_file)
        raise


def cleanup_old_backups():
    """Remove backups older than retention period"""
    cutoff_date = datetime.now() - timedelta(days=RETENTION_DAYS)
    
    try:
        backup_files = sorted(Path(BACKUP_DIR).glob('xsb_db_backup_*.sql*'))
        
        deleted_count = 0
        for backup_file in backup_files:
            # Extract date from filename
            filename = backup_file.name
            date_str = filename.replace('xsb_db_backup_', '').replace('.sql.gz', '').replace('.sql', '')
            
            try:
                backup_date = datetime.strptime(date_str, '%Y%m%d_%H%M%S')
                
                if backup_date < cutoff_date:
                    backup_file.unlink()
                    logger.info(f"🗑️  Deleted old backup: {backup_file.name}")
                    deleted_count += 1
            except ValueError:
                logger.warning(f"⚠️  Could not parse date from filename: {filename}")
        
        if deleted_count > 0:
            logger.info(f"Cleanup completed: Removed {deleted_count} old backups")
        else:
            logger.info("No old backups to clean up")
            
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")


def list_backups():
    """List all available backups"""
    try:
        backup_files = sorted(Path(BACKUP_DIR).glob('xsb_db_backup_*.sql*'), reverse=True)
        
        if not backup_files:
            logger.info("No backups found")
            return
        
        logger.info("\n=== Available Backups ===")
        for backup_file in backup_files[:10]:  # Show last 10
            file_size = backup_file.stat().st_size / (1024 * 1024)
            mod_time = datetime.fromtimestamp(backup_file.stat().st_mtime)
            logger.info(f"📄 {backup_file.name} ({file_size:.2f} MB) - {mod_time}")
            
    except Exception as e:
        logger.error(f"Error listing backups: {e}")


def restore_backup(backup_file):
    """Restore database from backup file"""
    if not os.path.exists(backup_file):
        logger.error(f"Backup file not found: {backup_file}")
        return False
    
    logger.info(f"Starting restore from: {backup_file}")
    
    env = os.environ.copy()
    env['PGPASSWORD'] = DB_PASSWORD
    
    try:
        if backup_file.endswith('.gz'):
            # Decompress and restore
            cmd = f"gunzip -c {backup_file} | psql -h {DB_HOST} -p {DB_PORT} -U {DB_USER} -d {DB_NAME}"
            result = subprocess.run(
                cmd,
                shell=True,
                env=env,
                capture_output=True,
                text=True
            )
        else:
            # Direct restore
            cmd = [
                'psql',
                '-h', DB_HOST,
                '-p', DB_PORT,
                '-U', DB_USER,
                '-d', DB_NAME,
                '-f', backup_file
            ]
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True
            )
        
        if result.returncode == 0:
            logger.info(f"✅ Restore completed successfully")
            return True
        else:
            logger.error(f"❌ Restore failed: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Restore error: {e}")
        return False


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='PostgreSQL Database Backup Tool')
    parser.add_argument('action', nargs='?', default='backup',
                        choices=['backup', 'cleanup', 'list', 'restore'],
                        help='Action to perform')
    parser.add_argument('--file', help='Backup file to restore (for restore action)')
    
    args = parser.parse_args()
    
    ensure_backup_dir()
    
    try:
        if args.action == 'backup':
            backup_file = create_backup()
            cleanup_old_backups()
            logger.info("Backup routine completed")
            
        elif args.action == 'cleanup':
            cleanup_old_backups()
            
        elif args.action == 'list':
            list_backups()
            
        elif args.action == 'restore':
            if not args.file:
                logger.error("Please specify backup file with --file")
                exit(1)
            restore_backup(args.file)
            
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        exit(1)
