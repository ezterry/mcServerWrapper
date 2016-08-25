# mcServerWrapper

A small self contained python wrapper to aid in running a Minecraft server
instance.

This script is designed to run on on top of many versions of Minecraft thus
providing the same functionality to both modded and vanilla mc installations.

While other versions are expected to work it has been tested with:

* Forge for mc1.7.10
* mc1.8.3-1.8.7
* mc1.9.* with and without forge
* mc1.10.* with and without forge

## Features

* Curses interface, while very bare bones this allows typing a command outside
the stream of text from the server, and easy access to some log history
* Auto backups, Allow for timed backups to be automatically generated from
the running instance
* Restart on crash, If the Minecraft server crashes unexpectedly, try to start
it again, (will only happen if the server has been up 5mins, to prevent
thrashing)
* Advance Moderation, allow (via formatted chat commands) moderators to add,
remove, and list the white-list as well as kick players without granting other
cheats/op
* Basic discord integration, an experimental api link to discord to relay in
game chat to/from a discord channel

## Requirements

* Posix system for Curses
* Discord.py api https://github.com/Rapptz/discord.py
* Python3 to run the script
* Java (tested with openjdk8) for minecraft

## Configuration

The expected use case is to place serv.py in the same directory as the
minecraft/forge jar you intend to run

Once this is set up you can run the script './serv.py' and then at the prompt
type '!!stop' to exit without running minecraft. This will pre-generate the
'serv.py.ini' config file allowing you to set up your environment:

### Section: Java
* exec - The java executable, use 'java' if its on your path, otherwise this
lets you specify the version of java to run
* jar - the minecraft server jar file, example 'minecraft_server.1.10.2.jar' or
'forge-1.7.10-10.13.4.1566-1.7.10-universal.jar'

### Section: Backups
* directory - directory to generate the tar.gz backups into
* interval mins - the number of minutes between backups (note this is the time
from server ready to the first backup, and after a backup completes when the 
next begins.
* count - the number of historic backups to keep


### Section: misc
* world name - displayed in ui status bar, will be overwritten by the level-name
once the server starts if it they don't match.
* text buffer - lines of text to keep in the curses interface buffer that the
user can scroll with pageup/pagedown
* args - json list of the command line arguments to pass to minecraft, one 
argument per list entry

### Section: discord
* token - if blank discord integration is disabled, otherwise your bot token
from your discord application screen
* channel id - If the bot has access to this channel it will relay chat between
it and in game.

## User Guide

### Running the Server

To start the server once both serv.py and Minecraft's server.properties and 
eula.txt are properly configured run:

```
./serv.py
```

This will open up the curses interface, the wrapper command '!!start' will
then launch the minecraft server

### Adding moderators

To grant in game commands to your moderators you will need to add them via the
curses interface with the '!!perms' commands

List a users current permissions

```
!!perms list <Username>
```

add a permission to a user

```
!!perms add <permission> <Username>
```

remove a permission from a user

```
!!perms del <permission> <Username>
```

Current permissions are:
* kick - allows the mod to kick a user from the server
* whitelist - allows the mod to list, and edit the whitelist

Internally serv.py uses the UUIDs for tracking user permissions, thus if
a moderator changes their username they will automatically retain all
granted permissions

### Force a backup

If you want to force a backup prior to its scheduled time, run

```
!!backup
```

###mc server commands

With the exception of the '!!' prefixed commands all input to the curses
interface is relayed to the running instance of the minecraft server

Thus most minecraft commands will behave as normal

### Moderator Commands (in game)

When playing moderators may use chat to run special commands, to list help
for all commands your avatar has access to in game you would send the chat
message

```
/me !!help
```

This will message the user back with commands available to that user.

### Exiting Minecraft

If you need to stop the minecraft server, and exit the curses wrapper
use the 

```
!!stop
```
command

### Discord

Configuring discord integration:

* To set up discord integration you will first need to make a new application
with a bot at https://discordapp.com/developers/applications/me

* Neither a RedirectURI or RPC Origin is required for the application

* The bots token must be put into the serv.py.ini file

* Add the bot to your Guild (ie discord server) by replacing your application id with where the following URL has 'appid' https://discordapp.com/oauth2/authorize?client_id=appid&scope=bot&permissions=0

* Find you channel id (must be in a guild you added your bot to) if you don't 
know it you can run serv.py, on startup it will list all channels the discord
bot has access to with their ids, once you have it copy it into the ini file.

Once properly configured discord will automatically relay messages between the
discord service and in game chat.

From discord you can at mention the bot with 'online' to get the current players
logged into the server

example if you named your application mcbot:

```
@mcbot online
```
