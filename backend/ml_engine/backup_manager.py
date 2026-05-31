"""
Backup & Recovery Manager
Automated backups and disaster recovery
"""

import asyncio
import json
import pickle
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
import os
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get('MONGO_URL', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'gold_signals_v3')
BACKUP_DIR = Path('backups')


class BackupManager:
    """Manage backups and recovery"""
    
    def __init__(self):
        self.backup_dir = BACKUP_DIR
        self.backup_dir.mkdir(exist_ok=True)
        self.client = AsyncIOMotorClient(MONGO_URL)
        self.db = self.client[DB_NAME]
    
    async def backup_signals(self, days: int = 7) -> Dict[str, Any]:
        """
        Backup signals from last N days
        
        Args:
            days: Number of days to backup
        
        Returns:
            Backup metadata
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            
            # Query signals
            signals = await self.db.signals.find({
                'created_at': {'$gte': cutoff_date}
            }).to_list(None)
            
            # Create backup file
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            backup_file = self.backup_dir / f'signals_backup_{timestamp}.json'
            
            with open(backup_file, 'w') as f:
                json.dump(signals, f, default=str, indent=2)
            
            file_size_mb = backup_file.stat().st_size / (1024 * 1024)
            
            logger.info(
                f"✅ Backed up {len(signals)} signals to {backup_file.name} ({file_size_mb:.2f} MB)"
            )
            
            return {
                'success': True,
                'backup_file': str(backup_file),
                'signal_count': len(signals),
                'file_size_mb': round(file_size_mb, 2),
                'timestamp': datetime.utcnow().isoformat()
            }
        
        except Exception as e:
            logger.error(f"❌ Signal backup failed: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    async def backup_models(self, models: Dict[str, Any]) -> Dict[str, Any]:
        """
        Backup ML models
        
        Args:
            models: Dict of model_name -> model_object
        
        Returns:
            Backup metadata
        """
        try:
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            models_dir = self.backup_dir / f'models_{timestamp}'
            models_dir.mkdir(exist_ok=True)
            
            backed_up = []
            for model_name, model_obj in models.items():
                try:
                    model_file = models_dir / f'{model_name}.pkl'
                    with open(model_file, 'wb') as f:
                        pickle.dump(model_obj, f)
                    backed_up.append(model_name)
                    logger.info(f"✅ Backed up model: {model_name}")
                except Exception as e:
                    logger.error(f"❌ Failed to backup {model_name}: {str(e)}")
            
            return {
                'success': True,
                'backup_dir': str(models_dir),
                'models_backed_up': backed_up,
                'total_models': len(models),
                'timestamp': datetime.utcnow().isoformat()
            }
        
        except Exception as e:
            logger.error(f"❌ Model backup failed: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    async def backup_database(self) -> Dict[str, Any]:
        """
        Backup entire database
        
        Returns:
            Backup metadata
        """
        try:
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            backup_file = self.backup_dir / f'database_backup_{timestamp}.json'
            
            # Get all collections
            collections = await self.db.list_collection_names()
            
            backup_data = {}
            total_docs = 0
            
            for collection_name in collections:
                docs = await self.db[collection_name].find({}).to_list(None)
                backup_data[collection_name] = docs
                total_docs += len(docs)
                logger.info(f"✅ Backed up collection: {collection_name} ({len(docs)} docs)")
            
            # Save backup
            with open(backup_file, 'w') as f:
                json.dump(backup_data, f, default=str, indent=2)
            
            file_size_mb = backup_file.stat().st_size / (1024 * 1024)
            
            logger.info(
                f"✅ Database backup complete: {total_docs} documents, {file_size_mb:.2f} MB"
            )
            
            return {
                'success': True,
                'backup_file': str(backup_file),
                'collections': collections,
                'total_documents': total_docs,
                'file_size_mb': round(file_size_mb, 2),
                'timestamp': datetime.utcnow().isoformat()
            }
        
        except Exception as e:
            logger.error(f"❌ Database backup failed: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    async def restore_signals(self, backup_file: str) -> Dict[str, Any]:
        """
        Restore signals from backup
        
        Args:
            backup_file: Path to backup file
        
        Returns:
            Restore metadata
        """
        try:
            with open(backup_file, 'r') as f:
                signals = json.load(f)
            
            # Insert into database
            if signals:
                result = await self.db.signals.insert_many(signals)
                logger.info(f"✅ Restored {len(result.inserted_ids)} signals")
            
            return {
                'success': True,
                'signals_restored': len(signals),
                'timestamp': datetime.utcnow().isoformat()
            }
        
        except Exception as e:
            logger.error(f"❌ Signal restore failed: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def cleanup_old_backups(self, days: int = 30) -> Dict[str, Any]:
        """
        Delete backups older than N days
        
        Args:
            days: Age threshold in days
        
        Returns:
            Cleanup metadata
        """
        try:
            cutoff_time = datetime.utcnow() - timedelta(days=days)
            deleted_files = []
            
            for backup_file in self.backup_dir.glob('*'):
                if backup_file.is_file():
                    file_time = datetime.fromtimestamp(backup_file.stat().st_mtime)
                    if file_time < cutoff_time:
                        backup_file.unlink()
                        deleted_files.append(backup_file.name)
                        logger.info(f"✅ Deleted old backup: {backup_file.name}")
            
            logger.info(f"✅ Cleanup complete: {len(deleted_files)} files deleted")
            
            return {
                'success': True,
                'files_deleted': deleted_files,
                'count': len(deleted_files),
                'timestamp': datetime.utcnow().isoformat()
            }
        
        except Exception as e:
            logger.error(f"❌ Backup cleanup failed: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def list_backups(self) -> Dict[str, Any]:
        """List all available backups"""
        try:
            backups = []
            for backup_file in sorted(self.backup_dir.glob('*'), reverse=True):
                if backup_file.is_file():
                    file_size_mb = backup_file.stat().st_size / (1024 * 1024)
                    file_time = datetime.fromtimestamp(backup_file.stat().st_mtime)
                    
                    backups.append({
                        'filename': backup_file.name,
                        'path': str(backup_file),
                        'size_mb': round(file_size_mb, 2),
                        'created_at': file_time.isoformat()
                    })
            
            logger.info(f"✅ Found {len(backups)} backups")
            
            return {
                'success': True,
                'backups': backups,
                'count': len(backups),
                'timestamp': datetime.utcnow().isoformat()
            }
        
        except Exception as e:
            logger.error(f"❌ Backup listing failed: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }


# Global backup manager instance
backup_manager = BackupManager()


async def scheduled_backup():
    """Run scheduled backups (call from cron job)"""
    logger.info("🔄 Starting scheduled backup...")
    
    # Backup signals
    signals_result = await backup_manager.backup_signals(days=7)
    logger.info(f"Signals backup: {signals_result}")
    
    # Backup database
    db_result = await backup_manager.backup_database()
    logger.info(f"Database backup: {db_result}")
    
    # Cleanup old backups
    cleanup_result = backup_manager.cleanup_old_backups(days=30)
    logger.info(f"Cleanup: {cleanup_result}")
    
    return {
        'signals': signals_result,
        'database': db_result,
        'cleanup': cleanup_result
    }

