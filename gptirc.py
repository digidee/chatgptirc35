import openai
import asyncio
import ssl
import functools
from collections import namedtuple
import json



Message = namedtuple("Message", "prefix command params")
Prefix = namedtuple("Prefix", "nick ident host")

def send_line_to_writer(writer: asyncio.StreamWriter, line):
    print("->", line)
    writer.write(line.encode("utf-8") + b"\r\n")

def send_cmd_to_writer(writer: asyncio.StreamWriter, cmd, *params):
    params = list(params)
    if params:
        if " " in params[-1]:
            params[-1] = ":" + params[-1]
    params = [cmd] + params
    send_line_to_writer(writer, " ".join(params))

def send_msg(writer: asyncio.StreamWriter, target, msg):
    send_cmd_to_writer(writer, "PRIVMSG", target, msg)


def parse_line(line):
    prefix = None

    if line.startswith(":"):
        prefix, line = line.split(None, 1)
        name = prefix[1:]
        ident = None
        host = None
        if "!" in name:
            name, ident = name.split("!", 1)
            if "@" in ident:
                ident, host = ident.split("@", 1)
        elif "@" in name:
            name, host = name.split("@", 1)
        prefix = Prefix(name, ident, host)

    command, *line = line.split(None, 1)
    command = command.upper()

    params = []
    if line:
        line = line[0]
        while line:
            if line.startswith(":"):
                params.append(line[1:])
                line = ""
            else:
                param, *line = line.split(None, 1)
                params.append(param)
                if line:
                    line = line[0]

    return Message(prefix, command, params)

    


async def irc_client(**options):
    global messages
    messages = [{"role": "system", "content": f"{options['system_message']}"}]

    # Initialize the OpenAI API client
    openai.api_key = options.get("api_key")

    # Set up the connection to the IRC server
    server = options.get("server")
    port = options.get("port")
    ssl_type = options.get("ssl")

    if(options.get("allow_self_signed")):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ssl_type = ctx

    # Connect to the IRC server using SSL
    reader, writer = await asyncio.open_connection(host=server, port=port, ssl=ssl_type)
    print(f"Connected to the IRC server {server}")
    
    # Send the initial commands to join the channel
    sendline = functools.partial(send_line_to_writer, writer)
    sendcmd = functools.partial(send_cmd_to_writer, writer)

    sendline("NICK {nickname}".format(**options))
    sendline("USER {ident} * * :{realname}".format(**options))
    
    # Continuously read incoming messages
    while not reader.at_eof():
        line = await reader.readline()
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError:
            line = line.decode("latin1")

        line = line.strip()
        if line:
            message = parse_line(line)
            if message.command.isdigit() and int(message.command) >= 400:
                # error?
                print(message)

            if message.command == "PING":
                sendcmd("PONG", *message.params)
            elif message.command == "001":
                for channel in options["channels"]:
                    sendcmd("JOIN", channel)

            elif message.command == "PRIVMSG":
                target = str(message.params[0])  # channel or nick
                text = str(message.params[1])  # msg text
                host = str(message.prefix.host)  # user's hostname
                source = str(message.prefix.nick)  # nick
                to_source = f"{source}: "

                parts = text.split()

                if len(parts) == 0:
                    continue

                if target == f"{options['nickname']}" and parts[0] != "!system" and parts[0] != "!reset":
                    target = source
                    to_source = ""
                    sendcmd(
                        "PRIVMSG",
                        target,
                        to_source +f"{options['wating_message']}",
                    )
                    prompt = parts[0:]
                    prompt = " ".join(prompt)
                    await handle_message(sendcmd, target, to_source, prompt)

                if target == f"{options['nickname']}" and parts[0] == "!reset":
                    target = source
                    to_source = ""
                    sendcmd("PRIVMSG", target, to_source +f"{options['reset_message']}")
                    messages = [{"role": "system", "content": f"{options['system_message']}"}]
                    continue

                if target == f"{options['nickname']}" and parts[0] == "!system":
                    target = source
                    to_source = ""
                    prompt = parts[1:]
                    prompt = " ".join(prompt)
                    messages = [{"role": "system", "content": f"{prompt}"}]
                    sendcmd("PRIVMSG", target, to_source +"Chat GPT system context changed")
                    sendcmd("PRIVMSG", target, to_source +f"{messages}")
                    continue

                if target != f"{options['nickname']}" and parts[0] == "!reset":
                    sendcmd("PRIVMSG", target, to_source +f"{options['reset_message']}")
                    messages = [{"role": "system", "content": f"{options['system_message']}"}]
                    continue

                if target != f"{options['nickname']}" and parts[0] == "!system":
                    prompt = parts[1:]
                    prompt = " ".join(prompt)
                    messages = [{"role": "system", "content": f"{prompt}"}]
                    sendcmd("PRIVMSG", target, to_source +"Chat GPT system context changed")
                    sendcmd("PRIVMSG", target, to_source +f"{messages}")
                    continue

                if len(parts) <= 1:
                    continue


                if parts[0] == f"{options['nickname']}:":
                    sendcmd(
                        "PRIVMSG",
                        target,
                        to_source +f"{options['wating_message']}",
                    )
                    prompt = parts[1:]
                    prompt = " ".join(prompt)
                    await handle_message(sendcmd, target, to_source, prompt)


async def handle_message(sendcmd, target, to_source, message):
    # Respond to messages that start with "chatgpt: "
    
    prompt = message.replace(f"{options['nickname']}:", "")
    response = generate_response(prompt)
    
    #for line in response.split('\n\n'):
    for msg in parse_outgoing(response):
        sendcmd("PRIVMSG", target, to_source +f"{msg}")


def generate_response(prompt):
    global messages
    messages.append({"role": "user", "content": f"{prompt}"})
    #print(json.dumps(messages))
    # Generate a response using the OpenAI API
    try:
        response = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=messages)
        message = response['choices'][0]['message']['content']
        return message
    except:
        return "We couldn't get a response for you, please try again"


def parse_outgoing(message):
    lines = message.split("\n")
    messages = []
    for line in lines:
        if len(line) > 400:
            words = line.split(" ")
            current_message = ""
            for word in words:
                if len(current_message) + len(word) + 1 <= 400:
                    current_message += word + " "
                else:
                    messages.append(current_message)
                    current_message = word + " "
            messages.append(current_message)
        else:
            messages.append(line)

    while "" in messages:
        messages.remove("")

    return messages


def send_irc_command(writer: asyncio.StreamWriter, command):
    writer.write(f"{command}\r\n".encode("utf-8"))
    asyncio.ensure_future(writer.drain())

with open("config.json", "r") as f:
    options = json.load(f)

# Start the asyncio event loop
asyncio.run(irc_client(**options))
