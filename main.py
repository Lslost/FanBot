import nonebot
import importlib.util

nonebot.init()

# 获取驱动并注册适配器
driver = nonebot.get_driver()
# if importlib.util.find_spec("nonebot.adapters.onebot.v11"): #onebot适配器
#     from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
#     driver.register_adapter(OneBotV11Adapter)
if importlib.util.find_spec("nonebot.adapters.milky"): #milky适配器
    from nonebot.adapters.milky import Adapter as MilkyAdapter
    driver.register_adapter(MilkyAdapter)
else:
    raise RuntimeError("未找到可用的适配器，请安装 onebot.v11 或 milky 适配器。")

# 加载插件
nonebot.load_from_json('plugin_index.json')

if __name__ == "__main__":
    nonebot.run()
