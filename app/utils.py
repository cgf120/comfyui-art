import os

from os import mkdir as os_mkdir

def download_file(url, path):
    """
    下载文件
    :param url: 文件URL
    :param path: 保存路径
    """
    import requests
    from requests.exceptions import RequestException

    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        with open(path, "wb") as file:
            for chunk in response.iter_content(chunk_size=1024):
                file.write(chunk)
    except RequestException as e:
        raise e