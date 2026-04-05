from dotenv import load_dotenv
import os

# 优先加载 postgres 测试环境
if os.path.exists(".env.postgres"):
    load_dotenv(".env.postgres")
else:
    load_dotenv()