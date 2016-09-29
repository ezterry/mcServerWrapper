#!/usr/bin/python3

import curses
import locale
import os
import time
import threading
import subprocess
import sched
import shutil
import select
import re
import os.path
import json
import configparser
import discord
import asyncio
import traceback

mc_jvm = "java"
mc_jar = "minecraft.jar"
backup_dir = "backups"
backup_interval = 1440 #12h in mins
backup_count = 2 #number of images to keep

server_args = ["-Xmx768M",
              "-Xms768M",
              "-Djava.net.preferIPv4Stack=true"
              ]
max_lines_buf = 1200
world_name = "World"

discord_token = ""
discord_channel = "000"

def readConfig():
    parser = configparser.ConfigParser()
    
    if(os.path.isfile("serv.py.ini")):
        parser.read('serv.py.ini')
    
    def getValue(section, name, default):
        if(section not in parser):
            parser[section]={}
        if(name not in parser[section]):
            parser[section][name]=default
        return(parser[section][name])
        
    def getIntValue(section, name, default):
        try:
            return int(getValue(section,name,str(default)))
        except ValueError:
            print(section + ":" + name + " is a string not an int")
            print("using default value: " + str(default))
            parser[section][name]=str(default)
            return default
    
    globals()["mc_jvm"] = getValue("java","exec",mc_jvm)
    globals()["mc_jar"] = getValue("java","jar",mc_jar)
    
    globals()["backup_dir"] = getValue("backups","directory",backup_dir)
    globals()["backup_interval"] = getIntValue("backups","interval mins",backup_interval)
    globals()["backup_count"] = getIntValue("backups","count",backup_count)
    
    globals()["world_name"] = getValue("misc","world name",world_name)
    globals()["max_lines_buf"] = getIntValue("misc","text buffer",max_lines_buf)
    
    globals()["server_args"] = json.loads(getValue("misc","args",json.dumps(server_args)))
    
    globals()["discord_token"] = getValue("discord","token",discord_token)
    globals()["discord_channel"] = getValue("discord","channel id",discord_channel)
    
    with open('serv.py.ini','w') as fp:
        parser.write(fp)

class mc_system:
    def __init__(self,screen):
        self.screen = screen
        screen.nodelay(True)
        (self.height,self.width) = screen.getmaxyx()
        
        self.outputscr=curses.newwin(self.height - 4,self.width,0,0)
        self.statusscr=curses.newwin(3,self.width,self.height-4,0)
        self.inputscr=curses.newwin(1,self.width,self.height-1,0)
        
        self.sched=sched.scheduler(time.time, time.sleep)
        self.inputbuff = ""
        self.histbuff = []
        self.pos = 0
        self.shuttingdown = False
        self.subproc = []
        self.inqueue = []
        self.minecraft = None
        self.autorestart = False
        self.systemUp = False
        self.perms = scriptPerms()
        self.backupLock=False
        self.updatescreen=True
        self.currentpid=None
        self.discord = None
        self.onlineusers = []
        
    def run(self):
        self.itert=0
        self.sched.enter(0.1,5,self.frame)
        self.updateStatus()
        self.updateBuffer()
        self.updateInput()
        if(discord_token != ""):
            self.discord = SDiscordRelay(self)
            self.discord.start()
        self.sched.run(self)
        
   
    def pushQueue(self,type,event):
        """push an event to the future queue"""
        self.inqueue.append([type,event])
        
    def popQueue(self,type):
        newqueue = []
        for nm,e in self.inqueue:
            if(type is None or nm==type):
                try:
                    self.sched.cancel(e)
                except ValueError:
                    pass
            else:
                newqueue.append([nm,e])
        self.inqueue=newqueue
      
    def startMC(self):
        #clean queue
        self.popQueue("minecraft")
        if(self.minecraft is not None):
            self.appendLine("Error minecraft is already running")
            return
        self.autorestart = False
        
        #create the full server command
        server_cmd=[]
        server_cmd.append(mc_jvm)
        server_cmd += server_args
        server_cmd.append("-jar")
        server_cmd.append(mc_jar)
        server_cmd.append("nogui")
        
        self.subproc.append(SSubProc(server_cmd,self.sched,
                                self.gameOutput,
                                self.minecraftShutdown))
        p=self.subproc[-1]
        self.sched.enter(0,1,self.appendLine,
                         argument=("Minecraft starting..",))
        self.minecraft=p
        p.start()
        def echopid():
            try:
                pid=p.getServerPid()
                self.currentpid = pid
                self.appendLine("Minecrft pid: " + str(pid))
                e=self.sched.enter(300,1,self.enableAutoRestart)
                self.pushQueue("autorestart",e)
            except:
                pass
        self.sched.enter(0.2,1,echopid)
        
    def minecraftShutdown(self,proc):
    
        self.appendLine("Minecraft terminated")
        self.subproc.remove(proc)
        self.minecraft = None
        self.systemUp = False
        self.currentpid = None
        if(self.autorestart and not self.shuttingdown):
            self.autorestart = False
            e=self.sched.enter(10,1,self.startMC)
            self.pushQueue("minecraft",e)            
    
    def enableAutoRestart(self):
        self.popQueue("autorestart")
        if(self.minecraft is None):
            self.appendLine("Auto Restart can only be enabled while minecraft is running")
            return
        if(not self.systemUp):
            self.appendLine("Auto restart canceled system not yet up")
            return
        self.autorestart = True
        self.appendLine("Auto Restart Enabled")
        
    def removeGroupColor(self,name):
        #removes a group color codes from a player name
        text = name.split(chr(167))
        name=text.pop(0)
        for part in text:
            name+=part[1:]
        return name
        
    def gameOutput(self,proc,line):
        if(len(line) == 0):
            return
        while(line[-1] in ('\r','\n')):
            line=line[:-1]
            if(len(line)==0):
                return

        #output filters
        if(line.find("com.gildedgames.util.core.UtilCore:debugPrint") < 0):
            self.appendLine(line)
        else:
            if(line.find("for GG Util") < 0):
                self.appendLine(line)

        #scan output triggers
        m=re.search(r'^\[(\d+\:\d+\:\d+)\]\s\[([^\]]*)\]\:\s(.*)$',line)
        if(m is not None):
            time = m.group(1)
            level = m.group(2)
            msg = m.group(3)
        else:
            time=""
            level=""
            msg=""
        if(level.endswith("INFO")):
            if(not self.systemUp and msg.startswith("Done ")):
                self.appendLine("Go! Go! Go!  (system up)")
                self.systemUp = True
                if(backup_interval > 0):
                    e=self.sched.enter(backup_interval*60,4,self.runBackup)
                    self.pushQueue("backup_proc",e)
            elif(not self.systemUp and msg.startswith("Preparing level")):
                m = re.search(r'level\s+\"(.*)\"$',msg)
                if(m is not None and m.group(1) != world_name):
                    globals()["world_name"] = m.group(1)
                    self.appendLine("Updated world name: " +world_name)
            else:
                self.parseInfoMessage(time,msg)

    def parseInfoMessage(self,time,msg):
        parsedUserMessage = False
        #possible command
        m = re.search(r'^\*\s+([^\s+]+)\s\!\!([^\s+]+)(\s(.*))?$',msg)
        if(m is not None):
            args = m.group(4)
            if(args is None):
                args = ""
            self.runInGameCommand(time,self.removeGroupColor(m.group(1)),m.group(2),args)
        
        #whitelist updates
        m = re.search(r'^Removed ([^\s+]+) from the whitelist$',msg)
        if(m is not None):
            self.minecraft.sendLine("say " + m.group(1) + " has been removed from the whitelist")
        m = re.search(r'^Added ([^\s+]+) to the whitelist$',msg)
        if(m is not None):
            self.minecraft.sendLine("say " + m.group(1) + " has been added to the whitelist")
        m = re.search(r'^Could not add ([^\s+]+) to the whitelist$',msg)
        if(m is not None):
            self.minecraft.sendLine("say " + m.group(1) + " could not be added to the whitelist")

        #user join/leave game
        m = re.search(r'^([^\s+]+)\sleft the game',msg)
        if(m is not None):
            parsedUserMessage=True
            if(self.removeGroupColor(m.group(1)) in self.onlineusers):
                self.onlineusers.remove(self.removeGroupColor(m.group(1)))
            if(self.discord is not None):
                self.discord.relay("User **" + m.group(1) + "** Has left " + world_name)
        m = re.search(r'^([^\s+]+)\sjoined the game',msg)
        if(m is not None):
            parsedUserMessage=True
            self.onlineusers.append(self.removeGroupColor(m.group(1)))
            if(self.discord is not None):
                self.discord.relay("User **" + m.group(1) + "** Has joined " + world_name)

        #relay chat to discord
        m = re.search(r'^\*\s+([^\s+]+)\s(.*)$',msg)
        if(m is not None and self.discord is not None):
            if(m.group(1).lower() == "server" and m.group(2).lower().startswith("discord")):
                pass
            elif(self.discord is not None):
                self.discord.relay("* **" + m.group(1) + "**  " + m.group(2))
        m = re.search(r'^[\[\<](.+?)[\]\>]\s(.*)$',msg)
        if(m is not None):
            if(m.group(1).lower() == "server" and m.group(2).lower().startswith("discord")):
                pass
            elif(self.discord is not None):
                self.discord.relay("<**" +m.group(1) + "**> " + m.group(2))

        #relay achievements
        m = re.search(r'^([^\s+]+)\shas just earned the achievement \[(.*?)\]$',msg)
        if(m is not None):
            parsedUserMessage=True
            user=self.removeGroupColor(m.group(1))
            achievement = m.group(2)
            if(user in self.onlineusers and self.discord is not None):
                self.discord.relay("**" + user + "** has earned the achievement: __" + achievement + "__")

        #relay basic death messages to discord
        if(not parsedUserMessage):
            m = re.search(r'^([^\s+]+)\s(.*)$',msg)
            if(m is not None):
                parsedUserMessage=True
                user=self.removeGroupColor(m.group(1))
                note=self.removeGroupColor(m.group(2))
                if(note.startswith("lost connection")):
                   pass
                elif(user in self.onlineusers and self.discord is not None):
                   if(note.find("[") == -1 and note.find("{") == -1):
                       self.discord.relay("**" + user + "** " + note)

    def runInGameCommand(self,time,user,commandName,args):
        self.appendLine("Command: " + user + ", " + commandName + ", " + args)
        if(commandName == "whitelist"):
            m = re.search(r'^\s*(add|remove)\s+([^\s+]+)$',args);
            if(m is not None):
                cmd = m.group(1)
                player = m.group(2)
                if(self.perms.checkPerm(user,"whitelist")):
                    self.minecraft.sendLine("whitelist " + cmd + " " + player)
                else:
                    self.minecraft.sendLine("say " + user + ": you do not have permission to update the whitelist")
                return
            m = re.search(r'^\s*list$',args)
            if(m is not None):
                if(self.perms.checkPerm(user,"whitelist")):
                    lst=getWhitelist()
                    self.minecraft.sendLine("say The current whitelist: " + str(lst))
                else:
                    self.minecraft.sendLine("say " + user + ": you do not have permission to request the whitelist")
                return
            if(self.perms.checkPerm(user,"whitelist")):
                self.minecraft.sendLine("msg " + user + " usage: '/me !!whitelist <add|remove> <player>' to edit the whitelist")
                self.minecraft.sendLine("msg " + user + " usage: '/me !!whitelist <list>' to list the current whitelist")
            return
        
        if(commandName == "kick"):
            if(not self.perms.checkPerm(user,"kick")):
                self.minecraft.sendLine("say " + user + ": you do not have permission to kick another player")
                return
            m = re.search(r'^\s*([^\s+]+)(\s+.*)?$',args)
            if(m is not None):
                player = m.group(1)
                reason = m.group(2)
                if(reason is None or reason.strip() == ''):
                    self.minecraft.sendLine("kick " + player)
                else:
                    self.minecraft.sendLine("kick " + player + " " + reason.strip())
                    
        if(commandName == "help"):
            if(self.perms.checkPerm(user,"whitelist")):
                self.minecraft.sendLine("msg " + user + " usage: '/me !!whitelist <add|remove> <player>' to edit the whitelist")
                self.minecraft.sendLine("msg " + user + " usage: '/me !!whitelist <list>' to list the current whitelist")
            if(self.perms.checkPerm(user,"kick")):
                self.minecraft.sendLine("msg " + user + " usage: '/me !!kick <player> [reason]' kick the player")
            self.minecraft.sendLine("msg " + user + " usage: '/me !!help' shows this help message")
        
    def updateBuffer(self):
        h = self.height - 5
        i=0
        self.outputscr.clear()
        if(len(self.histbuff) <= h):
            for ln in(self.histbuff):
                if(len(ln) >= self.width):
                    ln=ln[:self.width-1]
                self.outputscr.addstr(i,0,ln)
                i+=1
        else:
            i=self.height - 6
            y=len(self.histbuff)-i-1-self.pos
            while(i >= 0):
                ln=self.histbuff[y+i]
                if(len(ln) >= self.width):
                    ln=ln[:self.width-1]
                try:
                    self.outputscr.addstr(i,0,ln)
                except:
                    raise(Exception("error working on: " + str(i) + "||" + ln + " " + str(self.height)))
                i-=1
        self.outputscr.refresh()
        
    def updateStatus(self):
        self.statusscr.clear()
        self.statusscr.hline(0,0,ord('-'),self.width)
        if(self.currentpid is None):
            self.statusscr.addstr(1,0,world_name)
        else:
            self.statusscr.addstr(1,0,
                  world_name + " (" + str(self.currentpid) + ")")
        self.statusscr.hline(2,0,ord('-'),self.width)
        self.statusscr.refresh()
        
    def updateInput(self):
        self.inputscr.clear()
        curbuff=self.inputbuff
        if(len(curbuff)>=self.width):
            curbuff="..." + curbuff[(-1 * (self.width - 4)):]
        self.inputscr.addstr(0,0,curbuff)
        self.inputscr.refresh()
    
    def appendLine(self,ln):
        self.updatescreen=True
        if(len(ln) >= (self.width)):
            self.appendLine(ln[:self.width-1])
            self.appendLine(ln[self.width-1:])
            return
        if(len(self.histbuff)+1 > max_lines_buf):
            self.histbuff.pop(0)
        self.histbuff.append(ln)
    
    def appendLineThreadsafe(self,ln):
        def push():
            for l in ln.split('\n'):
                self.appendLine(l)
        self.sched.enter(0.01,6,push)
        
    def processUserCmd(self,ln):
        self.appendLine(ln)
        if(ln.strip() == "!!stop"):
            self.shuttingdown=True
            if(self.minecraft is not None):
                self.appendLine("Shutting down MC: pid=" + 
                           str(self.minecraft.getServerPid()))
                self.minecraft.sendLine("stop")
            #clear queue
            self.popQueue(None)
            if(self.discord is not None):
                self.discord.safe_shutdown()
            self.appendLine("Queued Activities: " + str(self.sched.queue))
            for p in self.subproc:
                self.appendLine("subpid: " + str(p.getServerPid()))
        #(self,cmd,s,input_cb=None,terminate_cb=None):
        elif(ln.strip() == "!!date"):
            def date_stop(proc):
                self.subproc.remove(proc)
                
            self.subproc.append(SSubProc(["date",],self.sched,
                                lambda x,y: self.appendLine("@: " + y),
                                date_stop))
            self.subproc[-1].start()
        elif(ln.strip() == "!!start"):
            e=self.sched.enter(1,1,self.startMC)
            self.pushQueue("minecraft",e)
        elif(ln.strip() == "!!autorestart"):
            e=self.sched.enter(1,1,self.enableAutoRestart)
            self.pushQueue("autorestart",e)
        elif(ln.strip() == "!!backup"):
            e=self.sched.enter(1,4,self.runBackup)
            self.pushQueue("backup_proc",e)
        elif(ln.strip() == "!!fixdiscord"):
            if(discord_token != ""):
                if(self.discord is not None):
                    self.discord.safe_shutdown()
                    self.discord = None
                self.discord = SDiscordRelay(self)
                self.discord.start()
        elif(ln.strip().startswith("!!perms")):
            self.permsCmds(ln.strip()[8:].strip())
        elif(self.minecraft is not None):
            self.minecraft.sendLine(ln)
        
    
    def permsCmds(self,s):
        args = s.split(" ")
        if(len(args) == 0 or args[0]=="help" or args[0]==""):
            self.appendLine("Chat Mod Permissions:")
            self.appendLine("  !!perm list <user> - list a users permissions")
            self.appendLine("  !!perm add <permission> <user> - give user the permission")
            self.appendLine("  !!perm del <permission> <user> - remove a permission form a user")
            self.appendLine("")
            self.appendLine("Current available permissions: " + 
                            str(self.perms.valid_perm))
        elif(args[0]=="list"):
            try:
                p = self.perms.lsPerm(args[1])
                self.appendLine("Permissions for " + args[1] + ":")
                self.appendLine("   " + str(p))
            except ValueError:
                self.appendLine("User " + args[1] + " not found")
        elif(args[0]=="add"):
            if(args[1] not in self.perms.valid_perm):
                self.appendLine("Permission " + args[1] + " is not valid")
            else:
                try:
                    self.perms.addPerm(args[2],args[1])
                    self.appendLine("User: " + args[2] + "has been granted " + 
                                    args[1])
                except ValueError:
                    self.appendLine("User " + args[2] + " not found")
        elif(args[0]=="del"):
            try:
                self.perms.rmPerm(args[2],args[1])
                self.appendLine("User: " + args[2] + " is not permitted to use "+
                                args[1])
            except ValueError:
                self.appendLine("User " + args[2] + " not found")
        else:
            self.appendLine("Unknown permission command, please run '!!perms help'")
    def frame(self):
        #check resize:
        (nheight,nwidth) = self.screen.getmaxyx()
        if(nheight != self.height or nwidth != self.width):
            self.screen.clear()
            self.screen.refresh()
            (self.height,self.width) = (nheight,nwidth)
            self.outputscr=curses.newwin(self.height - 4,self.width,0,0)
            self.statusscr=curses.newwin(3,self.width,self.height-4,0)
            self.inputscr=curses.newwin(1,self.width,self.height-1,0)
            
            if(len(self.histbuff) <= (self.height - 5)):
                self.pos=0 
            elif(len(self.histbuff) - self.pos - (self.height-5) <=0 ):
                self.pos = len(self.histbuff) - self.height +4
            #self.updateStatus()
            #self.updateBuffer()
            #self.updateInput()
            self.updatescreen=True
        self.itert +=1
        #read input
        if(not self.shuttingdown):
            ch = self.screen.getch()
            while (ch != curses.ERR):
                if(ch == 263): #backspace
                    self.inputbuff = self.inputbuff[:-1]
                elif(ch == 410): #resize
                    pass
                elif(ch == 339): #pageup
                    self.pos+=5
                    if(len(self.histbuff) <= (self.height - 5)):
                        self.pos=0 
                    elif(len(self.histbuff) - self.pos - (self.height-5) <=0 ):
                        self.pos = len(self.histbuff) - self.height +4 
                elif(ch == 338): #pagedown
                    self.pos-=5
                    if(self.pos<0):
                        self.pos=0
                    pass
                elif(ch == 10): #newline
                    if(len(self.inputbuff) > 0):
                        self.processUserCmd(self.inputbuff)
                        self.inputbuff = ""
                else:
                    try:
                        ch=chr(ch)
                        self.inputbuff+=ch
                    except:
                        pass
                ch = self.screen.getch()
                self.updatescreen=True
        else:
            self.updatescreen=True
        if(self.updatescreen):
            self.updatescreen=False
            try:
                self.updateStatus()
                self.updateBuffer()
                self.updateInput()
            except:
                self.updatescreen=True
        if(self.shuttingdown and len(self.subproc) == 0):
            return
        self.sched.enter(0.1,5,self.frame)
        
    def runBackup(self):
        self.popQueue("backup_proc")
        t = time.localtime()
        filename = (world_name + "_" + 
                    str(t.tm_year).zfill(4) +
                    str(t.tm_mon).zfill(2) +
                    str(t.tm_mday).zfill(2) + "-" +
                    str(t.tm_hour).zfill(2) +
                    str(t.tm_min).zfill(2) +
                    str(t.tm_sec).zfill(2) + ".tar.gz")
        if(not os.path.isdir(backup_dir)):
            try:
                os.mkdir(backup_dir)
            except:
                self.appendLine("[Backup] Error with backups, backup dir does not exist and cannot be created")
                return
        
        if(not self.systemUp):
            #server must have restarted
            self.appendLine("[Backup] Backup canceled due to server state")
            return
        if(self.backupLock):
            self.appendLine("[Backup] Backup already in progress...")
            return
        
        #The sub component callbacks
        #Backup step 1 (stop autosave)
        def stop_autosave():
            self.appendLine("[Backup] Disable auto save..")
            self.minecraft.sendLine("save-off")
            self.sched.enter(1,3,force_save)
        
        #Backup step 2 (force a world save)
        def force_save():
            self.appendLine("[Backup] Force one last save..")
            self.minecraft.sendLine("save-all")
            self.sched.enter(2,6,mktarball)
        
        #Backup step 3 (generate tarball of world save)
        def mktarball():
            self.appendLine("[Backup] Generating Tar..")
            cmd = ["nice","-n","12","tar", "-cz", world_name]
            try:
                fp=open(os.path.join(backup_dir,filename),'wb')
            except:
                self.appendLine("[Backup] Unable to open backup file")
                self.minecraft.sendLine("save-on")
            
            #callback when a buffer is read from tar
            def write_buffer(p,ln):
                #self.appendLine("Writing block")
                try:
                    fp.write(ln)
                except:
                    pass
            #callback when tar terminates
            def close_buffer(p):
                self.appendLine("Close Tar")
                self.subproc.remove(p)
                try:
                    fp.close()
                except:
                    self.appendLine("[Backup] Error closing backupfile")
                self.sched.enter(2,3,cleanup)
                
            self.subproc.append(SSubProc(cmd,self.sched,
                                write_buffer,
                                close_buffer,binary=True))
            self.subproc[-1].start()
        #Backup step 4 (Cleanup extra backup tar.gz) 
        def cleanup():
            self.appendLine("[Backup] Cleanup and post backup save..")
            self.minecraft.sendLine("save-all")
            
            hist = list(filter(lambda x:x.endswith(".tar.gz"),os.listdir(backup_dir)))
            hist.sort()
            while(len(hist) > backup_count):
                ref = os.path.join(backup_dir,hist.pop(0))
                try:
                    os.unlink(ref)
                except:
                    self.appendLine("Could not delete: " + ref)
            self.sched.enter(2,3,restorestate)
        #Backup step 5 (restore state, save on, schedule next backup, ect)
        def restorestate():
            self.minecraft.sendLine("save-on")
            self.minecraft.sendLine("say System Backup Complete")
            
            e=self.sched.enter(backup_interval*60,4,self.runBackup)
            self.pushQueue("backup_proc",e)
            self.backupLock=False
        
        #kick off backup steps
        self.backupLock = True
        self.minecraft.sendLine("say Starting System Backup")
        self.sched.enter(1,3, stop_autosave)
        

def getWhitelist():
    users=[]
    with open("whitelist.json","r") as f:
        whitelist = f.read()
        for e in json.loads(whitelist):
            users.append(e['name'])
    return(users)
    
class scriptPerms:
    def __init__(self):
        self.valid_perm = ["whitelist","kick",]
        self.users = {}
        if(not os.path.isfile("perms_script.txt")):
            return
        with open("perms_script.txt","r") as f:
            for ln in f:
                m=re.search("^(.+),(.*)$",ln)
                if(m is not None):
                    u = m.group(1)
                    p = m.group(2)
                    if(u in self.users and p not in self.users[u]):
                        self.users[u].append(p)
                    elif(u not in self.users):
                        self.users[u]=[p,]
    def getUUID(self,user):
        with open("whitelist.json","r") as f:
            whitelist = f.read()
            for e in json.loads(whitelist):
                if(e['name'] == user):
                    return(e['uuid'])
        return None
    def addPerm(self,user,perm):
        if(perm not in self.valid_perm):
            raise(ValueError("Invalid permission"))
        uuid = self.getUUID(user)
        if(uuid is None):
            raise(ValueError("Unknown User"))
        if(uuid in self.users and perm not in self.users[uuid]):
            self.users[uuid].append(perm)
        elif(uuid not in self.users):
            self.users[uuid]=[perm,]
        self.writePerm()

    def rmPerm(self,user,perm):
        uuid = self.getUUID(user)
        if(uuid is None):
            raise(ValueError("Unknown User"))
        if(uuid in self.users):
            if(perm in self.users[uuid]):
                self.users[uuid].remove(perm)
        self.writePerm()
    
    def lsPerm(self,user):
        uuid = self.getUUID(user)
        if(uuid is None):
            raise(ValueError("Unknown User"))
        if(uuid in self.users):
            if(uuid in self.users):
                return(list(self.users[uuid]))
            else:
                return([])
        
    def checkPerm(self,user,perm):
        uuid = self.getUUID(user)
        if(uuid is None):
            return False
        if(uuid in self.users):
            if(perm in self.users[uuid]):
                return True
        return False
    
    def writePerm(self):
        with open("perms_script.txt","w") as f:
            for uuid in self.users:
                for p in self.users[uuid]:
                    f.write(str(uuid) + "," + str(p) + "\n")
            f.flush()   

class SSubProcException(Exception):
    pass
    
class SSubProc(threading.Thread):
    """Subprocess input relay thread"""
            
    def __init__(self,cmd,s,input_cb=None,terminate_cb=None,binary=False):
        threading.Thread.__init__(self)
        self.procRunning = False
        self.procCompleate = False
        self.procPid = None
        self.procInput = None
        self.proc_cmd=cmd
        self.input_cb = input_cb
        self.terminate_cb = terminate_cb
        self.daemon = True
        self.sched = s
        self.binary = binary
        
    def waitForStart(self):
        while(self.procRunning is False):
            if(self.procCompleate):
                raise(SSubProcException("Process already complete"))
            time.sleep(0.1)
        
    def getServerPid(self):
        self.waitForStart()
        return(self.procPid)
    
    def getServerStdin(self):
        self.waitForStart()
        return(self.procInput)
    
    def sendLine(self,ln):
        self.waitForStart()
        self.procInput.write(ln.encode("utf-8") + b"\n")
        self.procInput.flush()
        
    def run(self):
        try:
            buf=160
            serr = subprocess.STDOUT
            if(self.binary):
                buf=512
                serr = subprocess.DEVNULL
            p=subprocess.Popen(self.proc_cmd,shell=False,bufsize=buf,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=serr, close_fds=False)
        except:
            self.procCompleat=True
            if(self.terminate_cb is not None):
                self.sched.enter(0,2,self.terminate_cb,argument=(self,))
            raise
        time.sleep(0.1)
        self.procPid = p.pid
        self.procInput= p.stdin
        self.procRunning = True
        pause_read = 0
        
        prio=2
        while(True):
            if(not self.binary):
                ln = p.stdout.readline()
                ln=str(ln,"utf-8")
                if(ln == ""):
                    self.procCompleate=True
                    self.procRunning=False
                    break
            else:
                if(pause_read >= 300):
                    time.sleep(0.1)
                    pause_read=0
                else:
                    pause_read+=1
                ln = p.stdout.read(512)
                if(ln == b""):
                    self.procCompleate=True
                    self.procRunning=False
                    break
                    
            if(self.input_cb is not None):
                self.sched.enter(0,prio,self.input_cb,argument=(self,ln,))
            prio+=1
        if(self.terminate_cb is not None):
            self.sched.enter(0,2,self.terminate_cb,argument=(self,))
        p.stdout.close()
        p.stdin.close()
        p.wait()
        return

class SDiscordRelay(threading.Thread):
    def __init__(self,par):
        threading.Thread.__init__(self)
        self.client = None
        self.serverchan = None
        self.mc = par
        self.membercache = {}
        
    def main(self):
        self.client = discord.Client(loop=asyncio.new_event_loop())
        asyncio.set_event_loop(self.client.loop)
        client = self.client
        
        @client.event
        @asyncio.coroutine
        def on_ready():
            self.mc.appendLineThreadsafe('Logged in as')
            self.mc.appendLineThreadsafe(self.client.user.name)
            self.mc.appendLineThreadsafe('------')
            self.running = True
            for chan in self.client.get_all_channels():
                self.mc.appendLineThreadsafe(chan.name + " - " + chan.id)
                if(discord_channel == chan.id):
                    self.mc.appendLineThreadsafe("Found " + chan.name + " channel")
                    self.serverchan=chan
    
        @client.event
        @asyncio.coroutine
        def on_message(message):
            #message is not on our primary channel
            if(message.channel.id != self.serverchan.id):
                return
            #message is from ourself
            if(message.author.id == self.client.user.id):
                return
            
            
            #message is a request for an online list
            if(message.content.lower() == "<@" + str(self.client.user.id) + "> online"):
                self.mc.appendLineThreadsafe('Send online users to discord')
                self.mc.appendLineThreadsafe('Users currently online: ' + str(self.mc.onlineusers))
                self.client.loop.call_soon(self.sendmessage,'Users currently online: ' + str(self.mc.onlineusers))
            #message needs to be relayed to minecraft
            else:
                m ="<" + message.author.name + "> "
                m+=self._substitute_members(message.content)
                if(self.mc.minecraft is not None):
                    firstline=True
                    for section in m.split('\n'):
                        if(firstline):
                            self.mc.minecraft.sendLine("/say discord " + section)
                            firstline=False
                        else:
                            self.mc.minecraft.sendLine("/say discord  | " + section)
    
    def _substitute_members(self,mesg):
        while True:
            m = re.search(r'^(.*)\<\@(\d+)\>(.*)$',mesg)
            if(m is not None):
                if(m.group(2) not in self.membercache):
                    self._cache_members()
                if(m.group(2) in self.membercache):
                    mesg  = m.group(1) + "@" + self.membercache[m.group(2)]
                    mesg += m.group(3)
                else:
                    mesg  = m.group(1) + "@<" + m.group(2) +"?>"
                    mesg += m.group(3)
            else:
                break
        return mesg
        
    def _cache_members(self):
        #load members
        for member in self.client.get_all_members():
            self.membercache[member.id] = member.name
            
    def shutdown(self):
        self.running=False
        self.client.loop.create_task(self.client.close())
        #self.client.loop.stop()
    
    def sendmessage(self,m):
        self.client.loop.create_task(self.client.send_message(self.serverchan,m))
        
    def relay(self,s):
        #relay s to discord
        if(self.running and self.serverchan is not None):
            text = s.split(chr(167))
            s=text.pop(0)
            for part in text:
                s+=part[1:]
            #clean up message here
            self.client.loop.call_soon_threadsafe(self.sendmessage,s)
   
    def safe_shutdown(self):
        if(self.client is not None):
            self.client.loop.call_soon_threadsafe(self.shutdown)
        
    def run(self):
        self.mc.appendLineThreadsafe("run discord")
        
        try:
            self.running = False
            self.main()
            self.client.loop.run_until_complete(self.client.start(discord_token))
        except Exception as e:
            if(self.running):
                self.mc.appendLineThreadsafe("error: " + str(e))
                self.mc.appendLineThreadsafe(traceback.format_exc())
        
def main(scr):
    mc_system(scr).run()
            
if(__name__=='__main__'):
    locale.setlocale(locale.LC_ALL, '')
    code = locale.getpreferredencoding()
    readConfig()
    curses.wrapper(main)
