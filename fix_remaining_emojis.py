#!/usr/bin/env python3
"""
Fix remaining emoji characters by replacing them
"""
import re

# Read the file
with open('main_dashboard.py', 'r', encoding='utf-8') as f:
    content = f.read()

# More comprehensive emoji replacements
replacements = {
    # Package/Box emojis
    '📦': '[PACKAGE]',
    # Warning emojis  
    '⚠️': '[WARN]',
    '⚠': '[WARN]',
    # Add the satellite/antenna emoji causing the error
    '📡': '[ENDPOINT]',
    # Other common ones we might have missed
    '🍪': '[COOKIES]',
    '🚫': '[BLOCKED]',
    '🌍': '[WORLD]',
    '💡': '[IDEA]',
    '📺': '[TV]',
    '🎵': '[MUSIC]',
    '🎬': '[MOVIE]',
    '🔒': '[LOCK]',
    '🔓': '[UNLOCK]',
    '🛡️': '[SHIELD]',
    '🛡': '[SHIELD]',
    '⭐': '[STAR]',
    '✨': '[SPARKLE]',
    '🎪': '[CIRCUS]',
    '🎲': '[DICE]',
    '⚗️': '[LAB]',
    '⚗': '[LAB]',
    '🔍': '[SEARCH]',
    '🔎': '[SEARCH]'
}

# Apply replacements
for emoji, replacement in replacements.items():
    content = content.replace(emoji, replacement)

# Also use regex to catch any remaining emoji-like characters
emoji_pattern = re.compile(r'[\U0001F300-\U0001F9FF]|[\U00002600-\U000027BF]|[\U0001F1E0-\U0001F1FF]')
content = emoji_pattern.sub('[EMOJI]', content)

# Write back
with open('main_dashboard.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed remaining emoji characters")