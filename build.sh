#!/usr/bin/env bash
set -e

pip install -r backend/requirements.txt

cd backend/config
python manage.py collectstatic --no-input
