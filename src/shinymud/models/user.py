from shinymud.commands import *
from shinymud.modes.init_mode import InitMode
from shinymud.modes.build_mode import BuildMode
from shinymud.world import World
import re
import logging

class User(object):
    """This is a basic user object."""
        
    def __init__(self, conn_info, **args):
        self.conn, self.addr = conn_info
        self.name = args.get('name', self.conn)
        self.password = args.get('password', None)
        self.description = args.get('description','You see nothing special about this person.')
        self.inq = []
        self.outq = []
        self.quit_flag = False
        self.log = logging.getLogger('User')
        self.mode = InitMode(self)
        self.location = None
        if 'channels' in args:
            self.channels = dict([_.split('=') for _ in args['channels'].split(',')])
        else:
            self.channels = {'chat': True}
        self.inventory = []
        
        self.strength = args.get('strength', 0)
        self.intelligence = args.get('intelligence', 0)
        self.dexterity = args.get('dexterity', 0)
        
    def to_dict(self):
        d = {}
        d['channels'] = ",".join([str(key) + '=' + str(val) for key, val in self.channels.items()])
        d['name'] = self.name
        d['password'] = self.password
        d['strength'] = self.strength
        d['intelligence'] = self.intelligence
        d['dexterity'] = self.dexterity
        d['description'] = self.description
        return d
        
    def update_output(self, data):
        """Helpfully inserts data into the user's output queue."""
        self.outq.append(data)
    
    def get_input(self):
        """Gets raw input from the user and queues it for later processing."""
        try:
            new_stuff = self.conn.recv(256)
            self.inq.append(new_stuff.replace('\n', ''))
        except Exception, e:
            pass
    
    def send_output(self):
        """Sends all data from the user's output queue to the user."""
        try:
            sent_output = False
            while len(self.outq) > 0:
                self.conn.send(self.outq[0])
                del self.outq[0]
                sent_output = True
            if sent_output:
                self.conn.send(self.get_prompt())
        except Exception, e:
            # If we die here, it's probably because we got a broken pipe.
            # In this case, we should disconnect the user
            self.user_logout(True)
            print str(e)
    
    def get_prompt(self):
        if not self.mode:
            return '>'
        elif self.mode.name == 'InitMode':
            return ''
        elif self.mode.name == 'BuildMode':
            prompt = '<Build'
            if self.mode.edit_area:
                prompt += ' ' + self.mode.edit_area.name
            if self.mode.edit_object:
                prompt += ' ' + self.mode.edit_object.__class__.__name__ + ' ' + str(self.mode.edit_object.id)
            prompt += '>'
            return prompt
    
    
    def parse_command(self):
        """Parses the lines in the user's input buffer and then calls
        the appropriate commands (if they exist)"""
        
        while len(self.inq) > 0:
            raw_string = self.inq.pop(0)
            match = re.search(r'\s*(\w+)([ ](.+))?$', raw_string)
            if match:
                cmd_name, _, args = match.groups()
                cmd = command_list[cmd_name]
                if cmd:
                    cmd(self, args).execute()
                else:
                    # The command the user sent was invalid... tell them so
                    self.update_output("I don't understand \"%s\"\n" % raw_string)
    
    def do_tick(self):
        """What should happen to the user everytime the world ticks."""
        if self.quit_flag:
            self.user_logout()
        else:
            self.get_input()
            if not self.mode:
                self.parse_command()
            elif self.mode.active:
                self.mode.state()
                if not self.mode.active:
                    self.mode = None
    
    def user_logout(self, broken_pipe=False):
        #TO DO: Save the user to the database
        if not broken_pipe:
            self.conn.send('Bye!\n')
        self.conn.close()
        if self.location:
            self.location.user_remove(self)
        World.get_world().user_remove(self.name)
        WorldEcho(self, "%s has left the world." % self.get_fancy_name()).execute()
    
    def get_fancy_name(self):
        return self.name.capitalize()
    
    def set_mode(self, mode):
        if mode == 'build':
            self.mode = BuildMode(self)
        elif mode == 'normal':
            self.mode.active = False
    
    def item_add(self, item):
        """Add an item to the user's inventory."""
        self.inventory.append(item)
    
    def item_remove(self, item):
        """Remove an item from the user's inventory."""
        if item in self.inventory:
            self.inventory.remove(item)
    
    def go(self, room):
        """Go to a specific room."""
        if self.location:
            # Tell the old room you are leaving.
            self.location.user_remove(self)
        if self.location and self.location == room:
            self.update_output('You\'re already there.\n')
        else:
            self.location = room
            self.location.user_add(self)
            # Tell the new room you have arrived
            self.update_output(self.look_at_room())
    
    def check_inv_for_keyword(self, keyword):
        """Check all of the items in a user's inventory for a specific keyword.
        
        Return the item that matches that keyword, else return None."""
        for item in self.inventory:
            if keyword in item.keywords:
                return item
        return None
    
    def look_at_room(self):
        """Return this user's view of the room they are in."""
        exit_list = [key for key, value in self.location.exits.items() if value != None]
        xits = 'exits: None'
        if exit_list:
            xits = 'exits: ' + ', '.join(exit_list)
        users = ''
        for user in self.location.users.values():
            if user.name != self.name:
                users += user.get_fancy_name() + ' is here.\n'
        items = ''
        for item in self.location.items:
            items += item.title + '\n'
        look = """%s\n%s\n%s\n%s%s""" % (self.location.title, xits, self.location.description, users, items)
        return look
    