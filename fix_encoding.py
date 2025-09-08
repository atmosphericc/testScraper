#!/usr/bin/env python3
"""
Fix Windows encoding issues by replacing emoji characters
"""
import re

# Read the file
with open('main_dashboard.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Dictionary of emoji replacements
emoji_replacements = {
    '🚀': '[START]',
    '⚡': '[FAST]',
    '🎯': '[TARGET]', 
    '🔥': '[HOT]',
    '🧠': '[BRAIN]',
    '🔄': '[REFRESH]',
    '📈': '[STATS]',
    '📊': '[DATA]',
    '⏱️': '[TIME]',
    '✅': '[OK]',
    '🎭': '[STEALTH]',
    '❌': '[ERROR]',
    '⏳': '[WAIT]',
    '🌞': '[DAY]',
    '🌙': '[NIGHT]',
    '😴': '[TIRED]',
    '📡': '[ENDPOINT]',
    '🔐': '[SECURE]',
    '🛒': '[CART]',
    '💡': '[IDEA]',
    '📱': '[MOBILE]',
    '🎪': '[CIRCUS]',
    '🎲': '[DICE]',
    '⚗️': '[LAB]',
    '🔍': '[SEARCH]',
    '📺': '[TV]',
    '🎵': '[MUSIC]',
    '🎬': '[MOVIE]'
}

# Replace emojis
for emoji, replacement in emoji_replacements.items():
    content = content.replace(emoji, replacement)

# Write back
with open('main_dashboard.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed encoding issues by replacing emoji characters")