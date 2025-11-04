#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Проверка содержимого базы данных футбольных матчей

TODO: УДАЛИТЬ ПОСЛЕ ОТЛАДКИ - временный файл для проверки БД
"""
import sqlite3

conn = sqlite3.connect('football_matches.db')
cursor = conn.cursor()

cursor.execute('SELECT * FROM matches')
rows = cursor.fetchall()

print(f'Всего матчей в БД: {len(rows)}')
print('=' * 80)

for row in rows:
    print(f"ID: {row[0]}")
    print(f"  fixture_id: {row[1]}")
    print(f"  Команды: {row[3]} vs {row[4]}")
    print(f"  Фаворит: {row[5]}")
    print(f"  fav_team_id: {row[6]} (1=home, 0=away)")
    print(f"  Дата: {row[7]} {row[8]}")
    print(f"  Кэф: {row[9]}")
    print(f"  Статус: {row[10]}")
    print('-' * 80)

conn.close()
