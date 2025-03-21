import os

from os import mkdir as os_mkdir

# 从最下级创建目录，防止上级目录没有权限
def mkdir(path):
    if not os.path.exists(os.path.dirname(path)):
        mkdir(os.path.dirname(path))
    else:
        os_mkdir(path)