#!/bin/bash

# note: this script is supposed to be run from code root dir

# add cron job  with "crontab -e" command
## 0 */3 * * * cd /home/ubuntu/chatgpt_telegram_bot_pro && S3_DIR="s3://chatgpt-karfly-bot/mongodb_backup" bash scripts/mongodb_backup.sh
## note: S3_DIR is optional

# set the directory for the backups
BACKUP_DIR="/home/ubuntu/mongodb_backup"
mkdir -p $BACKUP_DIR

# create a directory based on the current date
DATE=$(date +%Y-%m-%d_%H-%M-%S)
BACKUP_FILENAME="$DATE.gz"

# create backup
sudo docker compose --env-file config/config.env exec -T mongo mongodump --archive --gzip > "$BACKUP_DIR/$BACKUP_FILENAME"
echo "Backup was successfully created and saved to $CURRENT_BACKUP_DIR"

# upload to S3
if [[ -v S3_DIR ]]; then
    echo "$CURRENT_BACKUP_DIR"
    aws s3 cp "$BACKUP_DIR/$BACKUP_FILENAME" "$S3_DIR/$BACKUP_FILENAME"
    echo "Backup $BACKUP_FILENAME was successfully uploaded to $S3_DIR"
fi

# remove backups older than 3 days
find $BACKUP_DIR/* -mtime +3 -exec rm -rf {} \;
