"""Shared standard-library imports for CI tests, including standalone suites."""
import base64
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
