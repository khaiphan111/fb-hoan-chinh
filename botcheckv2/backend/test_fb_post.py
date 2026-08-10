import urllib.request
import re

url = "https://www.facebook.com/zuck/posts/10114008298711461"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    res = urllib.request.urlopen(req)
    html = res.read().decode('utf-8')
    print("reaction_count:", re.search(r'"reaction_count":\s*(\d+)', html))
    print("comment_count:", re.search(r'"comment_count":\s*(\d+)', html))
    print("share_count:", re.search(r'"share_count":\s*(\d+)', html))
except Exception as e:
    print(e)
