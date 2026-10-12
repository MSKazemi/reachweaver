#!/usr/bin/env python3
"""Standalone bundled ReachWeaver CLI; runs against the current project directory."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reachweaver.cli import main
raise SystemExit(main())
