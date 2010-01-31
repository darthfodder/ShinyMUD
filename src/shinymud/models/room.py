from shinymud.models.room_exit import RoomExit
from shinymud.models.reset import Reset
from shinymud.modes.text_edit_mode import TextEditMode
from shinymud.lib.world import World
import logging
import re

dir_opposites = {'north': 'south', 'south': 'north',
                 'east': 'west', 'west': 'east',
                 'up': 'down', 'down': 'up'}

class Room(object):
         
    def __init__(self, area=None, id=0, **args):
        self.id = str(id)
        self.area = area
        self.name = args.get('name', 'New Room')
        self.description = args.get('description','This is a shiny new room!')
        self.items = []
        self.exits = {'north': None,
                      'south': None,
                      'east': None,
                      'west': None,
                      'up': None,
                      'down': None}
        self.npcs = []
        self.resets = {}
        self.users = {}
        self.dbid = args.get('dbid')
        self.log = logging.getLogger('Room')
        self.world = World.get_world()
        if self.dbid:
            self.load_exits()
            self.load_resets()
    
    def load_exits(self):
        rows = self.world.db.select(""" re.dbid AS dbid, 
                                        re.linked_exit AS linked_exit,
                                        re.direction AS direction,
                                        re.openable AS openable,
                                        re.closed AS closed,
                                        re.hidden AS hidden,
                                        re.locked AS locked,
                                        a.name AS to_area,
                                        r.id AS to_id,
                                        i.area AS key_area,
                                        i.id AS key_id
                                        FROM room_exit re
                                        INNER JOIN room r ON r.dbid = re.to_room
                                        INNER JOIN area a on a.dbid = r.area
                                        LEFT JOIN item i ON i.dbid = re.key
                                        WHERE re.room=?""", [self.dbid])
        for row in rows:
            row['from_room'] = self
            self.exits[row['direction']] = RoomExit(**row)
    
    def load_resets(self, reset_list=None):
        self.log.debug(reset_list)
        if not reset_list:
            reset_list = self.world.db.select('* FROM room_resets WHERE room=?', [self.dbid])
        for row in reset_list:
            row['room'] = self
            area = self.world.get_area(row['reset_object_area'])
            if area:
                obj = getattr(area, row['reset_type'] + "s").get(row['reset_object_id'])
                if obj:
                    row['obj'] = obj
                    self.resets[int(row['dbid'])] = Reset(**row)
        for reset in self.resets.values():
            if reset.container:
                if int(reset.container) in self.resets:
                    container = self.resets.get(int(reset.container))
                    reset.container = container
                    container.add_nested_reset(reset)
    
    def to_dict(self):
        d = {}
        d['id'] = self.id
        d['area'] = self.area.dbid
        d['name'] = self.name
        d['description'] = self.description
        if self.dbid:
            d['dbid'] = self.dbid
        return d
    
    @classmethod
    def create(cls, area=None, room_id=0):
        """Create a new room."""
        new_room = cls(area, room_id)
        return new_room
    
    def destruct(self):
        if self.dbid:
            self.world.db.delete('FROM room WHERE dbid=?', [self.dbid])
    
    def save(self, save_dict=None):
        if self.dbid:
            if save_dict:
                save_dict['dbid'] = self.dbid
                self.world.db.update_from_dict('room', save_dict)
            else:    
                self.world.db.update_from_dict('room', self.to_dict())
        else:
            self.dbid = self.world.db.insert_from_dict('room', self.to_dict())
    
    def __str__(self):
        nice_exits = ''
        for direction, value in self.exits.items():
            if value:
                nice_exits += '    ' + direction + ': ' + str(value) + '\n'
            else:
                nice_exits += '    ' + direction + ': None\n'
        resets = ''
        for reset in self.resets.values():
            resets += '\n    [%s] %s' % (str(reset.dbid), str(reset))
        if not resets:
            resets = 'None.'
            
        room_list = (' Room %s in Area %s ' % (self.id, self.area.name)
                     ).center(50, '-') + '\n'
        room_list += """name: %s
description: 
%s
exits: 
%s
resets: %s""" % (self.name, self.description, nice_exits, resets)
        room_list += '\n' + ('-' * 50)
        return room_list
    
    def reset(self):
        """Reset (or respawn) all of the items and npc's that are on this 
        room's reset lists.
        """
        self.clean_resets()
        room_id = '%s,%s' % (self.id, self.area.name)
        for item in self.items:
            if item.is_container():
                if item.spawn_id and (item.spawn_id.startswith(room_id)):
                    self.item_purge(item)
        present_obj = [item.spawn_id for item in self.items if item.spawn_id]
        present_obj.extend([npc.spawn_id for npc in self.npcs if npc.spawn_id])
        for reset in self.resets.values():
            if reset.spawn_id not in present_obj and \
               (reset.get_spawn_point() == 'in room'):
                if reset.reset_type == 'npc':
                    self.npcs.append(reset.spawn())
                else:
                    self.items.append(reset.spawn())
    
    def clean_resets(self):
        """Make sure that all of the resets for this room are valid, and
        remove the ones that aren't.
        """
        room_resets = self.resets.values()
        for reset in room_resets:
            # Make sure any resets that have nested resets still have their
            # container item_type -- if they don't, we need to remove them
            # and their nested resets, because otherwise things will break
            # when they try to add other items to a container object that
            # doesn't exist.
            if reset.nested_resets:
                if not reset.reset_object.is_container():
                    # Somehow the container item type was removed from this
                    # object (perhaps a builder edited it and forgot to
                    # remove this reset). We should delete all resets that
                    # were supposed to have objects spawned inside this one
                    for sub_reset in reset.nested_resets:
                        if int(sub_reset.dbid) in self.resets:
                            sub_reset.destruct()
                            del self.resets[int(sub_reset.dbid)]
                    reset.destruct()
                    del self.resets[int(reset.dbid)]
    
    def user_add(self, user):
        self.users[user.name] = user
        self.area.times_visited_since_reset += 1
    
    def user_remove(self, user):
        if self.users.get(user.name):
            del self.users[user.name]
    
    def set_name(self, name, user=None):
        """Set the name of a room."""
        if not name:
            return 'Set the name to what?'
        name = ' '.join([name.strip().capitalize() for name in name.split()])
        self.name = name
        self.save({'name': self.name})
        return 'Room %s name set.' % self.id
    
    def set_description(self, args, user=None):
        """Set the description of a room."""
        user.last_mode = user.mode
        user.mode = TextEditMode(user, self, 'description', self.description)
        return 'ENTERING TextEditMode: type "@help" for help.\n'
    
    def new_exit(self, direction, to_room, **exit_dict):
        if exit_dict:
            new_exit = RoomExit(self, direction, to_room, **exit_dict)
        else:
            new_exit = RoomExit(self, direction, to_room)
        new_exit.save()
        self.exits[direction] = new_exit
    
    def set_exit(self, args, user=None):
        args = args.split()
        if len(args) < 3:
            return 'Usage: set exit <direction> <attribute> <value(s)>. Type "help exits" for more detail.\n'
        my_exit = self.exits.get(args[0])
        if my_exit:
            if hasattr(my_exit, 'set_' + args[1]):
                return getattr(my_exit, 'set_' + args[1])(args[2:])
            else:
                return 'You can\'t set that.\n'
        else:
            return 'That exit doesn\'t exist.\n'
    
    def add_exit(self, args):
        exp = r'(?P<direction>(north)|(south)|(east)|(west)|(up)|(down))([ ]+to)?([ ]+(?P<room_id>\d+))([ ]+(?P<area_name>\w+))?'
        match = re.match(exp, args, re.I)
        message = 'Type "help exits" to get help using this command.\n'
        if match:
            direction, room_id, area_name = match.group('direction', 'room_id', 'area_name')
            area = World.get_world().get_area(area_name) or self.area
            if area:
                room = area.get_room(room_id)
                if room:
                    self.new_exit(direction, room)
                    message = 'Exit %s created.\n' % direction
                else:
                    message = 'That room doesn\'t exist.\n'
            else:
                message = 'That area doesn\'t exist.\n'
        return message
    
    def add_reset(self, args):
        exp = r'((for[ ]+)?(?P<obj_type>(item)|(npc))([ ]+(?P<obj_id>\d+))' +\
              r'(([ ]+from)?([ ]+area)([ ]+(?P<area_name>\w+)))?' +\
              r'(([ ]+((in)|(into)|(inside)))?([ ]+reset)?([ ]+(?P<container>\d+)))?)'
        match = re.match(exp, args, re.I)
        if match:
            obj_type, obj_id, area_name, container = match.group('obj_type', 
                                                                 'obj_id', 
                                                                 'area_name',
                                                                 'container')
            area = World.get_world().get_area(area_name) or self.area
            if not area:
                return 'That area doesn\'t exist.\n'
            obj = getattr(area, obj_type + "s").get(obj_id)
            if not obj:
                return '%s number %s does not exist.\n' % (obj_type, obj_id)
            if container:
                if int(container) not in self.resets:
                    return 'Reset %s doesn\'t exist.\n' % container
                container_reset = self.resets.get(int(container))
                c_obj = container_reset.reset_object
                if container_reset.reset_object.is_container():
                    reset = Reset(self, obj, obj_type, container_reset)
                    reset.save()
                    container_reset.add_nested_reset(reset)
                    self.resets[reset.dbid] = reset
                    return 'A room reset has been added for %s number %s.\n' % (obj_type, obj_id)
                else:
                    return 'Room reset %s is not a container.\n' % container
            reset = Reset(self, obj, obj_type)
            reset.save()
            self.resets[reset.dbid] = reset
            return 'A room reset has been added for %s number %s.\n' % (obj_type, obj_id)
        return 'Type "help resets" to get help using this command.\n'
    
    def remove_reset(self, args):
        exp = r'(?P<reset_num>\d+)'
        match = re.match(exp, args, re.I)
        if not match:
            return 'Type "help resets" to get help using this command.\n'
        reset_id = int(match.group('reset_num'))
        if reset_id in self.resets:
            reset = self.resets[reset_id]
            del self.resets[reset_id]
            # If this reset has a container, we need to destroy
            # that container's link to it
            if reset.container and reset.container.dbid in self.resets:
                self.resets[reset.container.dbid].remove_nested_reset(reset)
            # Delete all resets that were supposed to be
            # reset into this container -- their spawn point is being deleted,
            # so they should no longer be on the reset list.
            message = 'Room reset %s has been removed.\n' % reset_id
            dependents = ', '.join([str(sub_reset.dbid) for sub_reset in reset.nested_resets])
            for sub_reset in reset.nested_resets:
                sub_reset.destruct()
                del self.resets[sub_reset.dbid]
            reset.destruct()
            if dependents:
                message += ('The following nested resets were also removed: ' + 
                            dependents + '.\n')
            return message
        return 'Room reset #%s doesn\'t exist.\n' % reset_id
    
    def remove_exit(self, args):
        if not args:
            return 'Which exit do you want to remove?\n'
        if not (args in self.exits and self.exits[args]):
            return '%s is not a valid exit.\n' % args
        exit = self.exits[args]
        link = ''
        if exit.linked_exit:
            # Clear any exit that is associated with this exit
            exit.to_room.exits[exit.linked_exit].destruct()
            exit.to_room.exits[exit.linked_exit] = None
            link = '\nThe linked exit in room %s, area %s, has also been removed.' % (exit.to_room.id,
                                                                                     exit.to_room.area.name)
        self.exits[args].destruct()
        self.exits[args] = None
        return args + ' exit has been removed.' + link + '\n'
    
    def link_exits(self, direction, link_room):
        """Link exits between this room (self), and the room passed."""
        this_exit = self.exits.get(direction)
        that_dir = dir_opposites.get(direction)
        that_exit = link_room.exits.get(that_dir)
        if this_exit:
            # If this exit already exists, make sure to unlink it with any other
            # rooms it may have been previously unlinked to, then change its to_room
            this_exit.unlink()
            this_exit.to_room = link_room
        else:
            self.new_exit(direction, link_room)
            this_exit = self.exits[direction]
        if that_exit:
            # If that exit was already linked, unlink it
            that_exit.unlink()
            that_exit.to_room = self
        else:
            link_room.new_exit(that_dir, self)
            that_exit = link_room.exits[that_dir]
        # Now that the exits have been properly created/set, set the exits to point to each other
        this_exit.linked_exit = that_exit.direction
        that_exit.linked_exit = this_exit.direction
        this_exit.save()
        that_exit.save()
        return 'Linked room %s\'s %s exit to room %s\'s %s exit.\n' % (this_exit.room.id, this_exit.direction,
                                                                      that_exit.room.id, that_exit.direction)
    
    def tell_room(self, message, exclude_list=[]):
        """Echo something to everyone in the room, except the people on the exclude list."""
        for person in self.users.values():
            if person.name not in exclude_list:
                person.update_output(message)
    
    def get_npc_by_kw(self, keyword):
        """Get an NPC from this room if its name is equal to the keyword given."""
        for npc in self.npcs:
            if keyword in npc.keywords:
                return npc
        return None
    
    def get_item_by_kw(self, keyword):
        """Get an item from this room they keyword given matches its keywords."""
        for item in self.items:
            if keyword in item.keywords:
                return item
        return None
    
    def get_user_by_kw(self, keyword):
        """Get a user from this room if their name is equal to the keyword given."""
        for user in self.users.values():
            if keyword == user.name:
                return user
        return None
    
    def check_for_keyword(self, keyword):
        """Return the first instance of an item, npc, or player that matches the keyword.
        If nothing in the room matches the keyword, return None."""
        # check the items in the room first
        item = self.get_item_by_kw(keyword)
        if item: return item
        
        # then check the npcs in the room
        npc = self.get_npc_by_kw(keyword)
        if npc: return npc
        
        # then check the PCs in the room
        user = self.get_user_by_kw(keyword)
        if user: return user
        
        # If we didn't match any of the above, return None
        return None
    
    def item_add(self, item):
        """Add an item to this room."""
        self.items.append(item)
    
    def item_remove(self, item):
        """Remove an item from this room."""
        if item in self.items:
            self.items.remove(item)
    
    def item_purge(self, item):
        """Delete this object from the room and the db, if it exists there."""
        if item in self.items:
            self.items.remove(item)
            if item.is_container():
                container = item.item_types.get('container')
                container.destroy_inventory()
            item.destruct()
    
    def npc_add(self, npc):
        self.npcs.append(npc)
    
    def npc_remove(self, npc):
        if npc in self.npcs:
            self.npcs.remove(npc)
    
    def purge_room(self):
        """Delete all objects and npcs in this room."""
        # When npcs are loaded into the room, they're not saved to the db
        # so we can just wipe the memory instances of them
        self.npcs = []
        # The items in the room may have been dropped by a user (and would
        # therefore have been in the item_inventory db table). We need
        # to make sure we delete the item from the db if it has an entry.
        for i in range(len(self.items)):
            self.item_purge(self.items[0])
    

