#!/bin/bash

env > /container-config/.cronenv
sed -i 's/\"/\\"/g' /container-config/.cronenv
cat /dev/urandom | tr -dc 'a-f0-9' | fold -w 32 | head -n 1 > /app/git_hash

# elevated permissions required
sudo /startup.sh

if [ $ENABLE_WEB == "True" ];
    then
echo "Starting Gunicorn"
# Start the second process
gunicorn boranga.wsgi --bind :8080 --config /app/gunicorn.ini
status=$?
if [ $status -ne 0 ]; then
  echo "Failed to start gunicorn: $status"
  exit $status
fi
else
   echo "ENABLE_WEB environment vairable not set to True, web server is not starting."
   /bin/bash
fi