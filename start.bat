@echo off
cd /d D:\django_projects\EasyStock
D:\Python\Python311\python.exe -m waitress --host=0.0.0.0 --port=8000 --threads=30 EasyStock.wsgi:application