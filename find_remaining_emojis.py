#!/usr/bin/env python3
"""
Find any remaining emoji characters in the file
"""
import re

# Read the file
with open('main_dashboard.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Look for any unicode characters that might be emojis (in various ranges)
emoji_pattern = re.compile(r'[\U0001F300-\U0001F9FF]|[\U00002600-\U000027BF]|[\U0001F1E0-\U0001F1FF]')

found_emojis = []
for line_num, line in enumerate(lines, 1):
    matches = emoji_pattern.findall(line)
    if matches:
        found_emojis.append((line_num, line.strip(), matches))

if found_emojis:
    print(f"Found {len(found_emojis)} lines with emoji characters:")
    for line_num, line, emojis in found_emojis:
        print(f"Line {line_num}: {emojis} -> {line[:100]}...")
else:
    print("No emoji characters found")

# Also check for specific problematic characters
problematic_chars = ['📦', '🍪', '⚠️', '🚫', '🌍', '💡', '📺', '🎵', '🎬']
for char in problematic_chars:
    if char in open('main_dashboard.py', 'r', encoding='utf-8').read():
        print(f"Found problematic character: {char}")