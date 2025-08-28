# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based Target product monitoring system that tracks inventory and can automatically attempt product purchases. The system uses async/await patterns with aiohttp for API requests and Playwright for browser automation.

## Common Commands

### Initial Setup
```bash
# Initialize directories and configuration
python setup.py init

# Setup Target login session (interactive)
python setup.py login

# Test setup completion
python setup.py test
```

### Running the Monitor
```bash
# Run in test mode (default - won't complete purchases)
python run.py

# Run in test mode explicitly
python run.py test

# Run in production mode (will attempt real purchases)
python run.py production
```

### Dashboard
```bash
# Start web dashboard (port 5000)
python dashboard/app.py
```

## Architecture Overview

### Core Components

**TargetMonitor** (`src/monitor.py`):
- Main orchestrator that coordinates all components
- Manages monitoring loops, batching, and rate limiting
- Handles configuration reloading and status tracking

**SessionManager** (`src/session_manager.py`):
- Manages Target.com authentication sessions using Playwright
- Validates session state periodically
- Handles interactive login setup

**StockChecker** (`src/stock_checker.py`):
- Queries Target's internal API for product availability
- Parses stock status from API responses
- Implements rate limiting and error handling

**BuyBot** (`src/buy_bot.py`):
- Automates purchase attempts using Playwright
- Supports test mode vs production mode
- Takes screenshots for debugging failed attempts

### Configuration Structure

**Product Configuration** (`config/product_config.json`):
- `products[]`: Array of products to monitor with TCIN, max price, quantity
- `settings.rate_limit`: Request throttling parameters
- `settings.purchase`: Purchase attempt behavior
- `settings.session`: Session validation settings
- `settings.logging`: Log configuration

### Key Directories

- `src/`: Core application modules
- `config/`: JSON configuration files
- `logs/`: Application logs and debug screenshots
- `sessions/`: Playwright session storage
- `dashboard/`: Flask web dashboard
- `target_profile/`: Chrome browser profile for automation

### Entry Points

- `run.py`: Main application entry point
- `setup.py`: Setup and initialization utilities  
- `dashboard/app.py`: Web dashboard server

### Dependencies

The project uses these key Python packages:
- `asyncio`: Async programming
- `aiohttp`: HTTP client for API requests
- `playwright`: Browser automation
- `flask`: Web dashboard
- `pathlib`: Path handling
- `logging`: Application logging

### Development Notes

- All main operations are async and use proper exception handling
- Rate limiting is implemented to avoid API throttling
- The system supports both test and production modes
- Session validation happens every 30 minutes
- Configuration is reloaded every 50 monitoring cycles
- Screenshots are taken for failed purchase attempts

### Testing

The system includes test files in `TESTFiles/` directory for validating stock checking functionality.

### Security Considerations

- Browser sessions are stored locally in `sessions/target_storage.json`
- Production mode requires explicit environment variables
- All purchase attempts are logged
- API keys and store IDs are hardcoded in source