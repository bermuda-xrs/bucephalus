import sys, tempfile, os; sys.path.insert(0, sys.argv[1])
from agent.security.capabilities import _path_strings
root = tempfile.mkdtemp(); os.mkdir(os.path.join(root, 'work'))
for v in [' file:///C:/Windows/win.ini', 'escaped.txt', 'newdir/x.txt', 'work/new.txt', 'file:///C:/Windows/win.ini', r'%USERPROFILE%\x', 'agent.db']:
    print(repr(v), list(_path_strings('note', v, frozenset(), True, root)))
