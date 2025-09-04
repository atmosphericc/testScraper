#!/usr/bin/env python3
"""
Project Cleanup Script
Moves outdated files to archive and creates clean project structure
"""

import os
import shutil
from pathlib import Path

def cleanup_project():
    """Clean up project structure by archiving outdated files"""
    
    # Files to archive (outdated dashboards, tests, etc.)
    files_to_archive = [
        # Outdated dashboard versions
        'dashboard_batch_api_integration.py',
        'dashboard_bulletproof.py', 
        'dashboard_f5_shape_evasion.py',
        'dashboard_fixed.py',
        'dashboard_integration.py',
        'dashboard_original_with_stealth.py',
        'dashboard_ultra_fast_stealth.py',
        'dashboard_ultra_fast_stealth_mock.py',
        'fix_dashboard.py',
        'simple_dashboard.py',
        'simple_dashboard_test.py',
        
        # Old test files (keep main ones)
        'test_advanced_batch_api.py',
        'test_aggressive_rate_limiting.py', 
        'test_batch_fulfillment_endpoint.py',
        'test_batch_stock_api.py',
        'test_current_api.py',
        'test_current_stock_api.py',
        'test_dashboard_api.py',
        'test_dashboard_debug.py',
        'test_dashboard_responsiveness.py',
        'test_endpoint_variations.py',
        'test_enhanced_stealth_system.py',
        'test_gist_access.py',
        'test_import_compatibility.py',
        'test_initial_stock_api.py',
        'test_integrated_system.py',
        'test_real_api.py',
        'test_retry_after_headers.py',
        'test_stock_with_location.py',
        'test_system_compatibility.py',
        'test_tcin_response.py',
        
        # Analysis and debug files
        'analyze_gist_content.py',
        'analyze_rate_limits.py',
        'debug_api.py',
        'extract_full_stock_response.py',
        'live_batch_api_call.py',
        
        # Old documentation
        'PROJECT_STRUCTURE.md',
        'README_CLEANUP.md',
        'ADVANCED_EVASION_SYSTEM.md',
        'HACKER_IMPROVEMENTS.md',
        'ULTIMATE_EVASION_GUIDE.md',
    ]
    
    # Directories to archive
    dirs_to_archive = [
        'dontNeed',
        'cache',
        'data',
    ]
    
    # Create archive directory structure
    archive_root = Path('archive')
    archive_root.mkdir(exist_ok=True)
    
    outdated_dir = archive_root / 'outdated_files'
    outdated_dir.mkdir(exist_ok=True)
    
    print("🧹 Cleaning up project structure...")
    
    # Archive outdated files
    archived_count = 0
    for file in files_to_archive:
        if Path(file).exists():
            try:
                shutil.move(file, outdated_dir / file)
                print(f"📦 Archived: {file}")
                archived_count += 1
            except Exception as e:
                print(f"❌ Failed to archive {file}: {e}")
    
    # Archive outdated directories
    for dir_name in dirs_to_archive:
        if Path(dir_name).exists():
            try:
                if (outdated_dir / dir_name).exists():
                    shutil.rmtree(outdated_dir / dir_name)
                shutil.move(dir_name, outdated_dir / dir_name)
                print(f"📦 Archived directory: {dir_name}")
                archived_count += 1
            except Exception as e:
                print(f"❌ Failed to archive directory {dir_name}: {e}")
    
    print(f"\n✅ Cleanup complete! Archived {archived_count} items")
    print(f"📁 Archived files moved to: {outdated_dir}")
    
    # Show clean project structure
    print("\n📊 Current clean project structure:")
    show_clean_structure()

def show_clean_structure():
    """Show the cleaned up project structure"""
    
    important_files = [
        'dashboard_ultimate_batch_stealth.py',  # Main dashboard
        'run.py',                              # Main runner
        'setup.py',                           # Setup script
        'simple_api_check.py',                # API testing
        'quick_evasion_test.py',              # Stealth testing  
        'test_ultimate_bypass.py',            # Ultimate testing
        'CLAUDE.md',                          # Documentation
        'README.md',                          # Main readme
    ]
    
    important_dirs = [
        'config/',                            # Configuration
        'src/',                              # Core modules
        'dashboard/',                        # Dashboard templates
        'sessions/',                         # Session storage
        'logs/',                            # Log files
        'archive/',                         # Archived files
    ]
    
    print("\n📁 KEY FILES:")
    for file in important_files:
        if Path(file).exists():
            print(f"  ✅ {file}")
        else:
            print(f"  ❌ {file} (missing)")
    
    print("\n📂 KEY DIRECTORIES:")  
    for dir_name in important_dirs:
        if Path(dir_name).exists():
            print(f"  ✅ {dir_name}")
        else:
            print(f"  ❌ {dir_name} (missing)")

if __name__ == '__main__':
    cleanup_project()