import asyncio
import json
import os
import aiohttp
import astrbot.api.message_components as Comp
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

@register("astrbot_plugin_consoleapi", "laopanmemz", "此插件可将AstrBot控制台的部分API接口，转为可由用户直接对话执行的注册指令（现已实现AstrBot重启和删除对话数据）", "1.0.0")
class Main(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.plugin_dir = os.path.join("data", "plugins", "astrbot_plugin_consoleapi")
        with open(os.path.join("data","config","astrbot_plugin_consoleapi_config.json"), 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
            self.base_url = data.get("base_url")
        self.allow_risk_operators = data.get("allow_risk_operators")
        self.config = self.context.get_config()
        self.username = self.config.get("dashboard").get("username")
        self.password = self.config.get("dashboard").get("password")
        if self.config.get("dashboard").get("host") == "0.0.0.0":
            self.host = "127.0.0.1"
        else:
            self.host = self.config.get("dashboard").get("host")
        self.port = self.config.get("dashboard").get("port")
        if self.base_url == "":
            self.base_url = f"http://{self.host}:{self.port}"
        self.login_body = {
            "username": self.username,
            "password": self.password
        }
        self.login_api = f"{self.base_url}/api/auth/login"
        self.restart_api = f"{self.base_url}/api/stat/restart-core"
        self.conversation_list_api = f"{self.base_url}/api/conversation/list"
        self.conversation_delete_api = f"{self.base_url}/api/conversation/delete"
        self.confirm = False
        self.matches = []
        self.waituser = False

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""

    async def login(self):
        """登录获取 token"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.login_api, json=self.login_body, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.error(f'登录失败 {resp.status} → {await resp.text()}')
                        return None
                    obj = await resp.json()
                    if obj.get('status') != 'ok':
                        logger.error(f'登录失败 → {obj}')
                        return None
                    token = obj['data']['token']
                    logger.info(f'登录成功，{token[:5]}*****{token[-5:]}')
                    auth_headers = {
                        'Authorization': f'Bearer {token}'
                    }
                    return auth_headers
        except Exception as e:
            logger.error(f'登录时出现错误: {e}')
            return None

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """用于重启指令执行完成后向用户发送执行结果"""
        if os.path.exists(os.path.join(self.plugin_dir, "lastmember.txt")):
            with open(os.path.join(self.plugin_dir, "lastmember.txt"), "r") as f:
                message_chain = MessageChain().message("AstrBot 已重启完毕。")
                lastmember = f.read()
                count = 0
                await asyncio.sleep(5)
                while count < 6:
                    try:
                        await self.context.send_message(lastmember, message_chain)
                        break
                    except Exception:
                        count += 1
                        logger.error(f'向用户发送消息时出现错误，可能目标适配器暂未启动，将稍后再试。（尝试次数：{count}/5）')
                        await asyncio.sleep(5)
                        continue
                f.close()
                os.remove(os.path.join(self.plugin_dir, "lastmember.txt"))

    async def auth(self, sender_id: str):
        """对操作用户鉴权"""
        if not self.allow_risk_operators:
            return True
        else:
            if sender_id in self.allow_risk_operators:
                return True
            else:
                return False

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("restart", alias={"重启"})
    async def restart(self, event: AstrMessageEvent):
        """API 下发重启指令"""
        with open(os.path.join(self.plugin_dir, "lastmember.txt"), "w") as f:
            f.write(event.unified_msg_origin)
            f.close()
        auth_headers = await self.login()
        yield event.plain_result("重启指令已下发。")
        async with aiohttp.ClientSession() as session:
            async with session.post(self.restart_api, headers=auth_headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                obj = await resp.json()
                if resp.status != 200:
                    logger.error(f'下发重启指令失败 {resp.status} → {obj}')
                    event.stop_event()
                    return
                if obj.get('status') != 'ok':
                    logger.error(f'重启失败 → {obj}')
                    event.stop_event()
                    return

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删除对话")
    async def delete_single_conversation(self, event: AstrMessageEvent, id: str):
        """删除单个对话"""
        if self.waituser:
            try:
                selected = self.matches[int(id) - 1]
                user_id = selected["user_id"]
                cid = selected["cid"]
                auth_headers = await self.login()
                json_data = {"user_id": user_id, "cid": cid}
                async with aiohttp.ClientSession() as session:
                    async with session.post(self.conversation_delete_api, headers=auth_headers, json=json_data,
                                            timeout=aiohttp.ClientTimeout(total=15)) as status:
                        obj = await status.json()
                        if status.status != 200:
                            logger.error(f'删除对话失败 {status.status} → {obj}')
                            self.waituser = False
                            event.stop_event()
                            return
                        if obj.get('status') != 'ok':
                            logger.error(f'删除对话失败 → {obj}')
                            self.waituser = False
                            event.stop_event()
                            return
                        logger.debug(f'删除对话成功：{obj}')
                        self.waituser = False
                        event.stop_event()
                        return
            except Exception:
                yield event.plain_result("序号错误。")
                self.waituser = False
                event.stop_event()
                return
        else:
            listdata = []
            auth_headers = await self.login()
            max_pages = 1000 # 安全限制，避免无限循环
            async with aiohttp.ClientSession() as session:
                count = 1
                while count <= max_pages:
                    async with session.get(f"{self.conversation_list_api}?page={count}&page_size=100", headers=auth_headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        obj = await resp.json()
                        data = obj["data"]["conversations"]
                        if not data:
                            break
                        listdata.extend(data)
                        count += 1

            for conv in listdata:
                user_id = conv["user_id"]
                if id in user_id:
                    self.matches.append(conv)

            if self.matches:
                chain = [Comp.Plain(
                    f"找到 {len(self.matches)} 个匹配的对话，请重新发送删除指令，并空格以参数附上要删除的目标序号数据：\n\n")]
                for index, match in enumerate(self.matches, start=1):
                    chain.append(Comp.Plain(f"\n\n{index}. {match['user_id']}\n\n"))
                self.waituser = True
                yield event.chain_result(chain)
                event.stop_event()
                return
            else:
                yield event.plain_result("未找到该用户对话。")
                event.stop_event()
                return

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删除所有对话数据")
    async def delete_confirm_conversation(self, event: AstrMessageEvent):
        """需先执行此命令才可发送二次确认指令"""
        self.confirm = True
        yield event.plain_result("请完整输入并发送二次确认指令「我知晓此操作为风险操作，操作后无法撤销或找回，执意删除所有对话数据」以确认删除。")
        event.stop_event()
        return

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("我知晓此操作为风险操作，操作后无法撤销或找回，执意删除所有对话数据")
    async def delete_all_conversation(self, event: AstrMessageEvent):
        """二次确认操作，需先执行前置命令才可执行此确认命令"""
        if not self.confirm:
            yield event.plain_result("未检测到前置操作命令。")
            event.stop_event()
            return
        if not await self.auth(event.get_sender_id()):
            logger.warning("无权限用户尝试执行风险操作，鉴权失败，终止执行。")
            event.stop_event()
            return
        auth_headers = await self.login()
        async with aiohttp.ClientSession() as session:
            async with session.get(self.conversation_list_api, headers=auth_headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                obj = await resp.json()
                listdata = obj["data"]["conversations"]
                for i in listdata:
                    user_id = i.get("user_id")
                    cid = i.get("cid")
                    json_data = {"user_id": user_id, "cid": cid}
                    async with session.post(self.conversation_delete_api, headers=auth_headers, json=json_data, timeout=aiohttp.ClientTimeout(total=15)) as status:
                        obj = await status.json()
                        if status.status != 200:
                            logger.error(f'删除对话失败 {status.status} → {obj}')
                            event.stop_event()
                            return
                        if obj.get('status') != 'ok':
                            logger.error(f'删除对话失败 → {obj}')
                            event.stop_event()
                            return
                        logger.debug(f'删除对话成功：{obj}')
                        event.stop_event()
                        return
        yield event.plain_result("已全部删除。")
        event.stop_event()
        return


    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""