import os
script_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(script_dir, "memory", "bad.txt"), "w", encoding="utf-8") as f:
    f.write("bad")

import shutil
shutil.copy2("mykey.py", os.path.join(script_dir, "mykey_backup.py"))
