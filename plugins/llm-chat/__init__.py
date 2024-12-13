from nonebot import on_message, on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent, GroupMessageEvent, Bot, Event
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State
from nonebot.rule import to_me
from nonebot.plugin import PluginMetadata
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from typing import List, Dict, Union
from .config import Config
import os
import json
from langgraph.checkpoint.memory import MemorySaver
from datetime import datetime
from nonebot.params import CommandArg, EventMessage, EventPlainText, EventToMe
from nonebot.exception import MatcherException
from random import choice
from .graph import build_graph, get_llm

__plugin_meta__ = PluginMetadata(
    name="LLM Chat",
    description="基于 LangGraph 的chatbot",
    usage="@机器人 或关键词，或使用命令前缀触发对话",
    config=Config,
)

plugin_config = Config.load_config()

os.environ["OPENAI_API_KEY"] = plugin_config.llm.api_key
os.environ["OPENAI_BASE_URL"] = plugin_config.llm.base_url
os.environ["GOOGLE_API_KEY"] = plugin_config.llm.google_api_key


def format_messages_for_print(messages: List[Union[SystemMessage, HumanMessage, AIMessage, ToolMessage]]) -> str:
    """
    格式化 LangChain 消息列表，提取并格式化 SystemMessage, HumanMessage, AIMessage (包括工具调用), 和 ToolMessage 的内容.
    """
    output = []
   
    for message in messages:
        if isinstance(message, SystemMessage):
            output.append(f"SystemMessage: {message.content}\n")
        elif isinstance(message, HumanMessage):
            output.append(f"HumanMessage: {message.content}\n")
        elif isinstance(message, AIMessage):
            if message.tool_calls:
                output.append(f"AIMessage: {message.content}\n")
                for tool_call in message.tool_calls:
                    output.append(f"  Tool Name: {tool_call['name']}\n")
                    try:
                        args = json.loads(tool_call['args'])
                    except (json.JSONDecodeError, TypeError):
                        args = tool_call['args']
                    output.append(f"  Tool Arguments: {args}\n")
            else:
                output.append(f"AIMessage: {message.content}\n")
        elif isinstance(message, ToolMessage):
                  output.append(f"ToolMessage: Tool Name: {message.name}  Tool content: {message.content}\n")
    return "".join(output)

# 会话模板
class Session:
    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.memory = MemorySaver()
        # 最后访问时间
        self.last_accessed = datetime.now()
        self.graph = None

# "group_123456_789012": Session对象1
sessions: Dict[str, Session] = {}

def get_or_create_session(thread_id: str) -> Session:
    """获取或创建会话"""
    if thread_id not in sessions:
        sessions[thread_id] = Session(thread_id)
    session = sessions[thread_id]
    session.last_accessed = datetime.now()
    return session

def cleanup_old_sessions():
    """按配置保存数量清理过期的会话"""
    if len(sessions) > plugin_config.plugin.max_sessions:
        # 按最后访问时间排序，删除最旧的会话
        sorted_sessions = sorted(
            sessions.items(),
            key=lambda x: x[1].last_accessed,
            reverse=True
        )
        # 保留配置指定数量的会话
        for thread_id, _ in sorted_sessions[plugin_config.plugin.max_sessions:]:
            del sessions[thread_id]

# 初始化模型和对话图
llm = get_llm()
graph_builder = build_graph(plugin_config, llm)


def chat_rule(event: Event) -> bool:
    Trigger_mode = plugin_config.plugin.Trigger_mode
    Trigger_words = plugin_config.plugin.Trigger_words

    msg = str(event.get_message())
    
    if "at" in Trigger_mode and event.is_tome():
        return True
    
    if "keyword" in Trigger_mode:
        for word in Trigger_words:
            if word in msg:
                return True

    if "prefix" in Trigger_mode:
        for word in Trigger_words:
            if msg.startswith(word):
                return True

    if not Trigger_mode:
        return event.is_tome()
    
    return False

chat_handler = on_message(rule=chat_rule, priority=10, block=True)

def remove_trigger_words(text: str, message: Message) -> str:
    """移除命令前缀(包括@和昵称)，保留关键词"""
    # 删除所有@片段
    text = str(message).strip()
    for seg in message:
        if seg.type == "at":
            text = text.replace(str(seg), "").strip()
    
    # 移除命令前缀
    if hasattr(plugin_config.plugin, 'Trigger_words'):
        for cmd in plugin_config.plugin.Trigger_words:
            if text.startswith(cmd):
                text = text[len(cmd):].strip()
                break
    
    return text

@chat_handler.handle()
async def handle_chat(
    # 提取消息全部对象
    event: MessageEvent,
    # 提取各种消息段
    message: Message = EventMessage(),
    # 提取纯文本
    plain_text: str = EventPlainText()
):
    
    # 检查群聊/私聊开关，判断消息对象是否是群聊/私聊的实例
    if isinstance(event, GroupMessageEvent) and not plugin_config.plugin.enable_group:
        await chat_handler.finish("不可以在群聊中使用")
    if not isinstance(event, GroupMessageEvent) and not plugin_config.plugin.enable_private:
        await chat_handler.finish("不可以在私聊中使用")

    image_urls = []
    for seg in message:
        if seg.type == "image" and seg.data.get("url"):
            image_urls.append(seg.data["url"])

    if event.reply:
        reply_message = event.reply.message
        for seg in reply_message:
            if seg.type == "image" and seg.data.get("url"):
                image_urls.append(seg.data["url"])

    # 处理消息内容,移除触发词
    full_content = remove_trigger_words(plain_text, message)
    
    # 如果全是空白字符,使用配置中的随机回复
    if not full_content.strip():
        if hasattr(plugin_config.plugin, 'empty_message_replies'):
            reply = choice(plugin_config.plugin.empty_message_replies)
            await chat_handler.finish(Message(reply))
        else:
            await chat_handler.finish("您想说什么呢?")
    
    if image_urls:
        full_content += "\n图片URL：" + "\n".join(image_urls)
    
    # 构建会话ID
    if isinstance(event, GroupMessageEvent):
        if plugin_config.plugin.group_chat_isolation:
            thread_id = f"group_{event.group_id}_{event.user_id}"
        else:
            thread_id = f"group_{event.group_id}"
    else:
        thread_id = f"private_{event.user_id}"

    print(f"Current thread: {thread_id}")
    
    cleanup_old_sessions()
    session = get_or_create_session(thread_id)

    # 如果当前会话没有图，则创建一个
    if session.graph is None:
        session.graph = graph_builder.compile(checkpointer=session.memory)

    try:
        result = session.graph.invoke(
           {"messages": [HumanMessage(content=full_content)]},
           config={"configurable": {"thread_id": thread_id}},
          )
        # print("LangGraph 返回的原始数据：")
        # print(result)
        # 打印格式化数据
        formatted_output = format_messages_for_print(result["messages"])
        print(formatted_output)
        # 如返回空消息
        if not result["messages"]:
              response = "对不起，我现在无法回答。"
        else:
           last_message = result["messages"][-1]
           if isinstance(last_message, AIMessage):
              if last_message.invalid_tool_calls:
                  response = f"工具调用失败: {last_message.invalid_tool_calls[0]['error']}" # 使用错误消息
              elif last_message.content:
                   response = last_message.content.strip()
              else:
                  response = "对不起，我没有理解您的问题。"
           elif isinstance(last_message, ToolMessage) and last_message.content:
              if isinstance(last_message.content,str):
                   response = last_message.content
              else:
                  response = str(last_message.content)
           else:
              response = "对不起，我没有理解您的问题。"
    except Exception as e:
        print(f"调用 LangGraph 时发生错误: {e}")
        response = f"抱歉，在处理您的请求时出现问题。错误信息：{e}"
    await chat_handler.finish(Message(response))




# 开关群聊会话隔离
group_chat_isolation = on_command(
    "chat group", 
    priority=5, 
    block=True, 
    permission=SUPERUSER,
)

@group_chat_isolation.handle()
async def handle_group_chat_isolation(args: Message = CommandArg(), event: Event = None):
    global plugin_config, sessions
    
    # 切换群聊会话隔离
    isolation_str = args.extract_plain_text().strip().lower()
    if not isolation_str:
        current_group = plugin_config.plugin.group_chat_isolation
        await change_model.finish(f"当前群聊会话隔离: {current_group}")
    
    if isolation_str == "true":
        plugin_config.plugin.group_chat_isolation = True
    elif isolation_str == "false":
        plugin_config.plugin.group_chat_isolation = False
    else:
        await group_chat_isolation.finish("请输入 true 或 false")

    # 清理对应会话
    keys_to_remove = []
    if isinstance(event, GroupMessageEvent):
        prefix = f"group_{event.group_id}"
        if plugin_config.plugin.group_chat_isolation:
            keys_to_remove = [key for key in sessions if key.startswith(f"{prefix}_")]
        else:
            keys_to_remove = [key for key in sessions if key == prefix]
    else:
       keys_to_remove = [key for key in sessions if key.startswith("private_")]

    for key in keys_to_remove:
        del sessions[key]


    await group_chat_isolation.finish(
        f"已{'禁用' if not plugin_config.plugin.group_chat_isolation else '启用'}群聊会话隔离，已清理对应会话"
    )






# 模型切换和清理历史会话
change_model = on_command(
    "chat model", 
    priority=5, 
    block=True, 
    permission=SUPERUSER,
)

@change_model.handle()
async def handle_change_model(args: Message = CommandArg()):
    global llm, graph_builder, sessions, plugin_config
    
    model_name = args.extract_plain_text().strip()
    if not model_name:
        try:
            current_model = llm.model_name
        except AttributeError:
            current_model = llm.model
        await change_model.finish(f"当前模型: {current_model}")
    
    try:
        llm = get_llm(model_name)
        graph_builder = build_graph(plugin_config, llm)
        sessions.clear()
        await change_model.finish(f"已切换到模型: {model_name}")
    except MatcherException:
        raise
    except Exception as e:
        await change_model.finish(f"切换模型失败: {str(e)}")