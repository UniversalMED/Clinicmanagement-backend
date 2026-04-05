#!/usr/bin/env bash
set -e

pip install -r requirements.txt

cd config
python manage.py collectstatic --no-input
