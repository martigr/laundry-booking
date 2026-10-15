import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'laundery'))
os.environ.setdefault('DATABASE', os.path.join(tempfile.mkdtemp(), 'import.db'))
