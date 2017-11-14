#!/usr/bin/env python

import sys,json,os,argparse,inspect,math,socket
from ConfigParser import SafeConfigParser
from collections import OrderedDict as odict

import requests
from PyQt4 import QtGui,QtCore

# Custom exception for catching communication errors with XBMC
class XBMCException(Exception):
  pass

# Custom file descriptor for use with SafeConfigParser to insert a dummy section
# (the library requires [Sections] in config file but I don't want any)
class FakeSecHead(object):
    def __init__(self, fp):
        self.fp = fp
        self.sechead = '[dummy]\n'
    def readline(self):
        if self.sechead:
            try: 
                return self.sechead
            finally: 
                self.sechead = None
        else: 
            return self.fp.readline()

################################################################################
# Main window class                                                            #
################################################################################

class Remote(QtGui.QMainWindow):

  def __init__(self,args):

    # default options
    self.DEFAULTS = odict([ ('xbmc_ip','127.0.0.1'),
                            ('xbmc_user',''),
                            ('xbmc_pass',''),
                            ('step_back',10),
                            ('step_fore',10),
                            ('def_plist',0) ])

    self.VALIDATORS = { 'xbmc_ip':ValidIP(),
                        'step_back':QtGui.QIntValidator(1,86400),
                        'step_fore':QtGui.QIntValidator(1,86400),
                        'def_plist':QtGui.QIntValidator(0,3) }

    # define keyboard shortcuts
    self.set_keys()

    # number of columns for the buttons
    self.COLS = 3

    super(Remote,self).__init__()
    self.parse_args(args)
    self.initUI()
    self.center()
    self.load_config()

    # update dictionaries with values loaded from config
    self.gen_key_dicts()
    
    self.show()

  def parse_args(self,args):
    """parse command line arguments; you can specify a config file with -c"""

    parser = argparse.ArgumentParser()
    d = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
    f = os.path.join(d,'remote.conf')
    parser.add_argument('-c',default=f,help='path to config file',metavar='file')
    args = parser.parse_args()
    self.conf_file = args.c

  def initUI(self):
    """create the main window UI including callbacks"""

    # create a file menu and add options to it
    menu = self.menuBar().addMenu('&File')
    self.make_item(menu,'Info','Ctrl+I')
    self.make_item(menu,'Playlist','Ctrl+P')
    self.make_item(menu,'Remote','Ctrl+R')
    self.make_item(menu,'Keybindings','Ctrl+K')
    self.make_item(menu,'Options...','Ctrl+O')

    # create the main grid where the buttons will be located
    grid = QtGui.QGridLayout()
    grid.setSpacing(10)
    area = QtGui.QWidget(self)
    area.setLayout(grid)
    self.setCentralWidget(area)

    # create the buttons as defined in set_keys()
    self.buttons = {}
    for (i,button) in enumerate(self.b_map.keys()):
      row = i/self.COLS
      col = i%self.COLS
      self.make_button(grid,button,row,col)

    # create the statusbar and disabled resizing
    self.statusBar().setSizeGripEnabled(False)

    # disable resizing on the main window, set title, and set focus
    self.setFixedSize(self.sizeHint())
    self.setWindowTitle('XBMC Remote')
    self.setFocus()

  def set_keys(self):
    """define keyboard shortcuts for the buttons"""
    
    # conf_names : config opts -> names
    # conf_map : names -> config opts
    # b_map : button names -> keys (initUI,makeButton)
    # s_map : shortcut names -> keys
    # b_keys : keys -> button names (keyPressEvent)
    # s_keys : keys -> shortcut names (keyPressEvent)

    # i manage two type of shortcuts, those tied to a button and those not
    # shortcuts not tied to a button have a function as the third tuple value
    opts = odict([('key_back',(QtCore.Qt.Key_Left,'Back')),
                  ('key_paus',(QtCore.Qt.Key_Space,'Pause')),
                  ('key_fore',(QtCore.Qt.Key_Right,'Fore')),
                  ('key_prev',(QtCore.Qt.Key_PageUp,'Prev')),
                  ('key_stop',(QtCore.Qt.Key_S,'Stop')),
                  ('key_next',(QtCore.Qt.Key_PageDown,'Next')),
                  ('key_vold',(QtCore.Qt.Key_Down,'Vol -')),
                  ('key_mute',(QtCore.Qt.Key_M,'Mute')),
                  ('key_volu',(QtCore.Qt.Key_Up,'Vol +')),
                  ('key_quit',(QtCore.Qt.Key_Escape,'Quit',self.close))])
    self.key_opts = opts
    self.conf_names = {}
    self.conf_map = {}
    for (k,v) in opts.items():
      self.DEFAULTS[k] = v[0]
      self.conf_names[k] = v[1]
      self.conf_map[v[1]] = k

    # create dictionaries to defaults
    self.gen_key_dicts()

  def gen_key_dicts(self):
    """generate dictionaries used at various points for key mapping"""

    # check if self.opts has been created yet
    opts = self.key_opts
    if hasattr(self,'opts'):
      for opt in opts:
        val = list(opts[opt])
        val[0] = int(self.opts[opt])
        opts[opt] = tuple(val)
    
    # this must be an OrderedDict so buttons are populated in order in __init__
    # most of these dictionaries are just for convenience in other functions
    self.b_map = odict([(v[1],v[0]) for v in opts.values() if len(v)<3])
    self.s_map = {v[1]:v[0] for v in opts.values() if len(v)>2}
    self.b_keys = {v:k for (k,v) in self.b_map.items()}
    self.s_keys = {v[0]:(v[1],v[2]) for v in opts.values() if len(v)>2}

  def get_shortcut(self,name):
    """return the shortcut for the given name"""

    # check both b_map and s_map
    if name in self.b_map:
      return self.b_map[name]
    if name in self.s_map:
      return self.s_map[name]
    return None

  def update_key(self,name,key):
    """update the key for the specified shortcut"""

    # update self.opts
    opt = self.conf_map[name]
    self.opts[opt] = key

    # check for a button shortcut and update related info
    if name in self.b_map:
      old = self.b_map[name]
      del self.b_keys[old]
      self.b_map[name] = key
      self.b_keys[key] = name
      self.buttons[name].setToolTip('Key: '+str(QtGui.QKeySequence(key).toString()))

    # check for a non-button shortcut and update related info
    if name in self.s_map:
      old_key = self.s_map[name]
      old_val = self.s_keys[old_key]
      del self.s_keys[old_key]
      self.s_map[name] = old_key
      self.s_keys[key] = (name,old_val[1])

  def make_button(self,grid,name,row,col):
    """helper function to create a button and add it to the grid"""
    
    button = QtGui.QPushButton(name,self)
    button.clicked.connect(self.cb_button)
    button.resize(button.sizeHint())
    key = self.b_map[name]
    button.setToolTip('Key: '+str(QtGui.QKeySequence(key).toString()))
    grid.addWidget(button,row,col)
    self.buttons[name] = button

  def make_item(self,menu,name,shortcut):
    """helper function to create a menu item and add it to the menu"""
    
    opts = QtGui.QAction(name,self)
    opts.setShortcut(shortcut)
    opts.triggered.connect(self.cb_menu)
    menu.addAction(opts)

  def center(self):
    """center the window on the current monitor"""

    # http://stackoverflow.com/a/20244839/2258915
    
    fg = self.frameGeometry()
    cursor = QtGui.QApplication.desktop().cursor().pos()
    screen = QtGui.QApplication.desktop().screenNumber(cursor)
    cp = QtGui.QApplication.desktop().screenGeometry(screen).center()
    fg.moveCenter(cp)
    self.move(fg.topLeft())

  def load_config(self):
    """read options from config file or set defaults"""

    # start with defaults, if the file does not exist write the defaults
    self.opts = odict([(k,str(v)) for (k,v) in self.DEFAULTS.items()])
    if not os.path.isfile(self.conf_file):
      self.save_config()
      return

    # read options from the config file into a dict
    conf = SafeConfigParser()
    result = conf.readfp(FakeSecHead(open(self.conf_file)))
    opts = {x:y for (x,y) in conf.items('dummy')}

    # update self.opts with options from the config file
    for opt in opts:
      if opt in self.opts:
        self.opts[opt] = opts[opt]

    # update shortcuts
    self.gen_key_dicts()

  def save_config(self):
    """save our current opts to the config file"""

    s = ''
    line = True
    for opt in self.opts:
      if line and opt.startswith('key'):
        s += '\n'
        line = False
      s += '%s = %s\n' % (opt,self.opts[opt])
    with open(self.conf_file,'w') as f:
      f.write(s)

  def keyPressEvent(self,e):
    """handle keyboard shortcuts"""

    key = e.key()
    if key in self.b_keys:
      self.cb_button(self.b_keys[key])
    if key in self.s_keys:
      self.s_keys[key][1]()

  def cb_menu(self):
    """handle menu item presses"""

    t = self.sender().text()
    if t=='Info':
      InfoDialog(self)
    elif t=='Playlist':
      PlaylistDialog(self)
    elif t=='Remote':
      RemoteDialog(self)
    elif t=='Keybindings':
      KeybindDialog(self)
    elif t=='Options...':
      OptsDialog(self)

  def cb_button(self,b=None):
    """handle button presses and set the statusbar message"""

    # if we don't do this the button will get focus break and keyboard shortcuts
    self.setFocus()

    # the param b is only set if called from keyPressEvent()
    if not b:
      b = self.sender().text()

    try:
      if b=='Back':
        msg = self.hop('back')
      elif b=='Pause':
        msg = self.playpause()
      elif b=='Fore':
        msg = self.hop('fore')
      elif b=='Prev':
        msg = self.jump('prev')
      elif b=='Stop':
        msg = self.stop()
      elif b=='Next':
        msg = self.jump('next')
      elif b=='Vol -':
        msg = self.volume('down')
      elif b=='Mute':
        msg = self.mute()
      elif b=='Vol +':
        msg = self.volume('up')
      else:
        return

    # catch xbmc communication errors to report them
    except XBMCException as e:
      msg = e.message
    self.statusBar().showMessage(msg)

  def playpause(self):
    """play/pause"""

    # execute method
    pid = self.xpid()
    speed = self.xbmc('Player.PlayPause',{'playerid':pid})['speed']

    # get current time and total time for statusbar message
    params = {'playerid':pid,'properties':['time','totaltime']}
    result = self.xbmc('Player.GetProperties',params)
    current = result['time']
    total = result['totaltime']

    # return statusbar message
    if speed==0:
      return 'Paused at %s / %s' % (time2str(current),time2str(total))
    left = time2sec(total)-time2sec(current)
    return 'Playing with %s / %s left' % (time2str(sec2time(left)),time2str(total))

  def hop(self,d):
    """hop slightly forward or backward in the video"""

    # get current time and total time
    x = int(self.opts['step_'+d])
    if d=='back':
      x = -x
    pid = self.xpid()
    params = {'playerid':pid,'properties':['time','totaltime']}
    result = self.xbmc('Player.GetProperties',params)

    # limit t to [0,totaltime]
    t = time2sec(result['time'])
    total = time2sec(result['totaltime'])
    t = min(total,max(0,t+x))
    t = sec2time(t)

    # execute method and return statusbar message
    self.xbmc('Player.Seek',{'playerid':pid,'value':t})
    return 'Seek to '+time2str(t)

  def stop(self):
    """stop playing"""
    
    pid = self.xpid()
    result = self.xbmc('Player.Stop',{'playerid':pid})
    return 'Stopped'

  def jump(self,d):

    # check playlist size
    pid = self.xpid()
    params = {'playlistid':pid,'properties':['size']}
    siz = self.xbmc('Playlist.GetProperties',params)['size']

    # execute method
    if d=='prev':
      params = {'playerid':pid,'to':'previous'}
      self.xbmc('Player.GoTo',params)
      if siz==0:
        return 'Jumped to beginning'
    if d=='next':
      if siz==0:
        return 'No playlist'
      params = {'playerid':pid,'to':'next'}
      self.xbmc('Player.GoTo',params)

    # return statusbar message
    params = {'playerid':pid,'properties':['position']}
    pos = self.xbmc('Player.GetProperties',params)['position']+1
    return 'Jumped to: %i / %i' % (pos,siz)

  def mute(self):
    """mute/unmute"""
    
    result = self.xbmc('Application.SetMute',{'mute':'toggle'})
    return {True:'Muted',False:'Unmuted'}[result]

  def volume(self,d):
    """adjust volume"""

    # get current volume
    d = {'down':-5,'up':5}[d]
    pid = self.xpid()
    params = {'properties':['volume']}
    result = self.xbmc('Application.GetProperties',params)
    vol = result['volume']

    # limit vol to [0,100] and execute method
    vol = min(100,max(0,vol+d))
    self.xbmc('Application.SetVolume',{'volume':vol})
    return 'Volume: '+str(vol)+'%'

  def xbmc(self,method,params=None):
    """make a request to the XBMC JSON-RPC web interface"""

    # build request
    p = {'jsonrpc':'2.0','id':1,'method':method}
    if params is not None:
      p['params'] = params
    url = 'http://'+self.opts['xbmc_ip']+'/jsonrpc'
    headers = {'content-type':'application/json'}
    payload = p
    params = {'request':json.dumps(payload)}
    usr = self.opts['xbmc_user']
    pw = self.opts['xbmc_pass']

    # catch ConnectionError exceptions from requests library
    try:
      r = requests.get(url,params=params,headers=headers,auth=(usr,pw))
    except Exception as e:
      raise XBMCException(e.__class__.__name__)

    # catch HTTP error responses (e.g. 401 Forbidden)
    if not r.ok:
      raise XBMCException('HTTP %i - %s' % (r.status_code,r.reason))

    # raise the JSON error message, or return the contents of the 'result' field
    r = json.loads(r.text)
    if 'error' in r:
      raise XBMCException(r['error']['message'])
    return r['result']
    
  def xpid(self):
    """helper method to get the id of the currently active player"""

    # returns any of [None,0,1,2]
    j = self.xbmc('Player.GetActivePlayers')
    if len(j)==0:
      return None
    return j[0]['playerid']

################################################################################
# Validators                                                                   #
################################################################################

class ValidIP(QtGui.QValidator):

  def validate(self,text,pos):
    """check for valid IP:port"""

    text = str(text)
    s = text
    if s=='':
      return (self.Intermediate,pos)
    
    # account for port
    if ':' in s:
      s = s.split(':')
      if len(s)>2:
        return (self.Invalid,pos)
      if s[1]=='':
        return (self.Intermediate,pos)
      if not s[1].isdigit():
        return (self.Invalid,pos)
      port = int(s[1])
      if port>65535:
        return (self.Invalid,pos)
      s = s[0]
    
    try:
      socket.inet_aton(s)
      return (self.Acceptable,pos)
    except:
      if ((text.replace(':','').replace('.','').isdigit()) and
          (len([x for x in text if x==':'])<2) and
          (len([x for x in text if x=='.'])<4)):
        return (self.Intermediate,pos)

    return (self.Invalid,pos)

################################################################################
# Remote dialog                                                                #
################################################################################

class RemoteDialog(QtGui.QDialog):

  def __init__(self,parent):

    super(RemoteDialog,self).__init__(parent)
    self.initUI()
    self.set_keys()
    self.show()
    self.setFocus()

  def set_keys(self):
    """create shortcuts"""

    self.keys = { QtCore.Qt.Key_Left      : 'Input.Left',
                  QtCore.Qt.Key_Right     : 'Input.Right',
                  QtCore.Qt.Key_Up        : 'Input.Up',
                  QtCore.Qt.Key_Down      : 'Input.Down',
                  QtCore.Qt.Key_Return    : 'Input.Select',
                  QtCore.Qt.Key_Escape    : 'Input.Back',
                  QtCore.Qt.Key_Backspace : ('Input.ExecuteAction','backspace')}

  def initUI(self):
    """create buttons"""

    # create grid layout
    grid = QtGui.QGridLayout()
    grid.setSpacing(10)
    self.setLayout(grid)

    # make buttons
    self.make_button(grid,'Context',0,0)
    self.make_button(grid,'Fullscreen',0,1)
    self.make_button(grid,'OSD',0,2)

    # disable resize and set title
    self.setFixedSize(self.sizeHint())
    self.setWindowTitle('Virtual Remote')

  def make_button(self,grid,name,row,col):
    """helper function to create a button and add it to the grid"""

    button = QtGui.QPushButton(name,self)
    button.clicked.connect(self.cb_button)
    button.resize(button.sizeHint())
    grid.addWidget(button,row,col)

  def cb_button(self):
    """handle button presses"""

    b = self.sender().text()
    xbmc = self.parent().xbmc
    if b=='Context':
      xbmc('Input.ContextMenu')
    elif b=='Fullscreen':
      xbmc('GUI.SetFullscreen',{'fullscreen':'toggle'})
    elif b=='OSD':
      xbmc('Input.ExecuteAction',{'action':'osd'})
    self.setFocus()

  def keyPressEvent(self,e):
    """handle keyboard shortcuts"""

    key = e.key()
    xbmc = self.parent().xbmc
    
    if key in self.keys:
      action = self.keys[key]
      if isinstance(action,tuple):
        xbmc(action[0],{'action':action[1]})
      else:
        xbmc(action)
    else:
      try:
        s = str(QtGui.QKeySequence(key).toString())
      except:
        return
      if len(s)==1 and ord(s)>31 and ord(s)<127:
        xbmc('Input.SendText',{'text':s,'done':False})

################################################################################
# Options dialog class                                                         #
################################################################################

class OptsDialog(QtGui.QDialog):

  def __init__(self,parent):
    
    super(OptsDialog,self).__init__(parent)
    self.initUI()
    self.setModal(True) # deny interaction with the main window until closed
    self.show()

  def initUI(self):
    """create labels and edit boxes"""

    # create grid layout
    grid = QtGui.QGridLayout()
    grid.setSpacing(10)
    self.setLayout(grid)

    # add QLabels and QLineEdits
    p = self.parent()
    opts = [x for x in p.opts.keys() if not x.startswith('key')]
    for (row,opt) in enumerate(opts):
      grid.addWidget(QtGui.QLabel(opt,self),row,0)
      box = QtGui.QLineEdit(p.opts[opt],self)
      if opt in p.VALIDATORS:
        box.setValidator(p.VALIDATORS[opt])
      grid.addWidget(box,row,1)

    # add OK and Cancel buttons
    row += 1
    self.make_button(grid,'OK',row,0)
    self.make_button(grid,'Cancel',row,1)

    # disabled resizing and set name
    self.setFixedSize(self.sizeHint())
    self.setWindowTitle('Remote Options')

  def make_button(self,grid,name,x,y):
    """helper function to add a button to the grid"""
    
    button = QtGui.QPushButton(name,self)
    button.clicked.connect(self.cb_button)
    button.resize(button.sizeHint())
    grid.addWidget(button,x,y)

  def cb_button(self):
    """catch button presses"""

    # only save config on 'OK' but close the dialog either way
    b = self.sender().text()
    if b=='OK':
      self.save_config()
    self.close()

  def save_config(self):
    """save the user-entered config"""

    # we can access the values in the text boxes by getting them from the grid
    p = self.parent()
    grid = self.layout()

    # update the main window's 'opts' dictionary then call its save_config()
    for i in range(0,grid.rowCount()-1):
      opt = grid.itemAtPosition(i,0).widget().text()
      val = grid.itemAtPosition(i,1).widget().text()
      p.opts[str(opt)] = str(val)
    p.save_config()

################################################################################
# Keybind dialog class                                                         #
################################################################################

class KeybindDialog(QtGui.QDialog):

  def __init__(self,parent):

    super(KeybindDialog,self).__init__(parent)
    self.initUI()
    self.show()

  def initUI(self):
    """create the list and buttons"""

    # create a grid layout
    grid = QtGui.QGridLayout()
    grid.setSpacing(10)
    self.setLayout(grid)

    # create the list
    lis = QtGui.QListWidget(self)
    self.lis = lis
    grid.addWidget(lis,0,0,1,2)
    self.names = []
    self.key_names = []
    p = self.parent()
    for (opt,name) in p.conf_names.items():
      if opt.startswith('key'):
        key = p.get_shortcut(name)
        key_name = str(QtGui.QKeySequence(key).toString())
        self.names.append(name)
        self.key_names.append(key_name)
        lis.addItem('%s = %s' % (name,key_name))

    # create the buttons
    self.make_button(grid,'Edit',1,0)
    self.make_button(grid,'OK',1,1)

    # disable resize and set title
    self.setFixedSize(self.sizeHint())
    self.setWindowTitle('Key Bindings')

  def make_button(self,grid,name,row,col):
    """helper function to create a button and add it to the grid"""

    button = QtGui.QPushButton(name,self)
    button.clicked.connect(self.cb_button)
    button.resize(button.sizeHint())
    grid.addWidget(button,row,col)

  def cb_button(self):
    """act on button presses"""

    b = self.sender().text()
    if b=='OK':
      self.parent().save_config()
      self.close()
    if b=='Edit':
      row = self.lis.currentRow()
      (name,key_name) = (self.names[row],self.key_names[row])
      KeypressDialog(self,name,key_name)

  def update_key(self,name,code,key_name):
    """update the given function name with the new code"""

    # update our internal lists and the listbox
    i = self.names.index(name)
    t = (name,key_name)
    self.names[i] = name
    self.key_names[i] = key_name
    text = '%s = %s' % t
    self.lis.item(i).setText(text)

    # send the update to our parent
    self.parent().update_key(name,code)

################################################################################
# Keypress dialog class                                                        #
################################################################################

class KeypressDialog(QtGui.QDialog):

  def __init__(self,parent,name,key_name):

    self.listen = False
    self.key_code = None
    self.name = name
    self.key_name = key_name

    super(KeypressDialog,self).__init__(parent)
    self.initUI()
    self.setModal(True)
    self.show()

  def initUI(self):
    """create labels and buttons"""

    # create a grid layout
    grid = QtGui.QGridLayout()
    grid.setSpacing(10)
    self.setLayout(grid)

    # create a label and textbox
    grid.addWidget(QtGui.QLabel(self.name+':',self),0,0)
    text = QtGui.QLineEdit(self.key_name,self)
    self.text = text
    text.setReadOnly(True)
    grid.addWidget(text,0,1)

    # add buttons
    self.buttons = {}
    self.buttons['record'] = self.make_button(grid,'Record',1,0)
    self.buttons['ok'] = self.make_button(grid,'OK',1,1)

    # disable resize and set title
    self.setFixedSize(self.sizeHint())
    self.setWindowTitle('Key Capture')

  def make_button(self,grid,name,row,col):
    """helper function to create a button and add it to the grid"""

    button = QtGui.QPushButton(name,self)
    button.clicked.connect(self.cb_button)
    button.resize(button.sizeHint())
    grid.addWidget(button,row,col)
    return button

  def cb_button(self):
    """act on button presses"""

    e = self.sender().text()

    # disable buttons, change text, set listen, set focus
    if e=='Record':
      [b.setDisabled(True) for b in self.buttons.values()]
      self.text.setText('PRESS')
      self.listen = True
      self.setFocus()

    # send result to parent window
    if e=='OK':
      p = self.parent()
      key_name = self.key_name

      # check for duplicates
      if key_name in p.key_names:
        self.key_name = p.key_names[p.names.index(self.name)]

        # if the key_name didn't change then just exit
        if self.key_name==key_name:
          self.close()
          return

        # reset the key_name in the textbox to its original value
        self.text.setText(self.key_name)
        i = p.key_names.index(key_name)
        name = p.names[i]

        # raise an error window
        error = QtGui.QMessageBox(self)
        error.critical(self,'Error','Key "%s" is bound to "%s"' % (key_name,name))
        error.setFixedSize(500,200)
        error.show

      # no duplicate, so update the key in our parent and close
      else:
        p.update_key(self.name,self.key_code,self.key_name)
        self.close()

  def keyPressEvent(self,e):
    """handle keyboard shortcuts"""

    if not self.listen:
      return

    # don't accept modifier keys
    try:
      self.key_code = e.key()
      self.key_name = str(QtGui.QKeySequence(e.key()).toString())
      [b.setEnabled(True) for b in self.buttons.values()]
      self.text.setText(self.key_name)
      self.listen = False
    except:
      pass

################################################################################
# Media info dialog class                                                      #
################################################################################

class InfoDialog(QtGui.QDialog):
  
  def __init__(self,parent):
    
    super(InfoDialog,self).__init__(parent)
    self.initUI()
    self.show()

  def initUI(self):
    """create the labels and text boxes"""

    # create a grid layout
    grid = QtGui.QGridLayout()
    grid.setSpacing(5)
    self.setLayout(grid)

    # for every entry returned by get_info() create a label and a textbox
    for (row,(k,v)) in enumerate(self.get_info().items()):
      grid.addWidget(QtGui.QLabel(k+':',self),row,0)
      label = QtGui.QLineEdit(v,self)
      label.setReadOnly(True)
      label.setCursorPosition(0)
      label.setMinimumWidth(300)
      grid.addWidget(label,row,1)

    # disable resizing and name the window
    self.setFixedSize(self.sizeHint())
    self.setWindowTitle('Media Info')

  def get_info(self):
    """return relevant info about the currently playing item in XBMC"""

    # we will return an OrderedDict to keep them in order
    info = odict()
    xbmc = self.parent().xbmc

    # check if anything is playing
    pid = self.parent().xpid()
    if pid is None:
      return {'Info':'Nothing playing.'}

    # get artist and album
    params = {'playerid':pid,'properties':['artist','album']}
    result = xbmc('Player.GetItem',params)['item']
    info['Title'] = get(result,'label','Unknown')
    artist = result.get('artist',['Unknown'])
    if len(artist)==0 or artist[0].strip()=='':
      artist = ['Unknown']
    info['Artist'] = artist[0]
    info['Album'] = get(result,'album','Unknown')

    # get playerid and media type
    result = xbmc('Player.GetActivePlayers')
    info['Player ID'] = str(result[0]['playerid'])
    info['Media'] = result[0]['type'].title()

    # get speed, time, and totaltime
    params = {'playerid':pid,'properties':['speed','time','totaltime']}
    result = xbmc('Player.GetProperties',params)
    info['Speed'] = {0:'Paused',1:'Playing'}[result['speed']]
    info['Current Time'] = time2str(result['time'])
    info['Total Time'] = time2str(result['totaltime'])

    # get current position in playlist
    params = {'playerid':pid,'properties':['position']}
    pos = xbmc('Player.GetProperties',params)['position']+1
    params = {'playlistid':pid,'properties':['size']}
    siz = xbmc('Playlist.GetProperties',params)['size']
    info['Playlist'] = '%i / %i' % (pos,siz)

    return info

################################################################################
# Playlist info dialog class                                                   #
################################################################################

class PlaylistDialog(QtGui.QDialog):
  
  def __init__(self,parent):
    
    super(PlaylistDialog,self).__init__(parent)
    self.initUI()
    self.show()

  def initUI(self):
    """create labels, drop-down menu, and main textbox"""

    default = int(self.parent().opts['def_plist'])

    # create grid layout
    grid = QtGui.QGridLayout()
    grid.setSpacing(10)
    self.setLayout(grid)

    # add a label for current playlist position and shuffled flag
    info = self.get_info()
    text = 'Current item: %s / %i' % (info['current'],len(info['items']))
    if info['shuffled']:
      text += ' (Shuffled)'
    grid.addWidget(QtGui.QLabel(text,self),0,0)

    # add a label describing the dropdown
    label = QtGui.QLabel('Display: ',self)
    label.setAlignment(QtCore.Qt.AlignRight|QtCore.Qt.AlignVCenter)
    grid.addWidget(label,0,1)

    # add the drop down menu
    self.disp_opts = ['Full Path','Filename','Title','Album - Title']
    box = QtGui.QComboBox(self)
    box.addItems(self.disp_opts)
    box.setCurrentIndex(default)
    box.currentIndexChanged.connect(self.cb_box)
    grid.addWidget(box,0,2)

    # add the main text box and disable editing
    items = QtGui.QTextEdit(self)
    items.setMinimumSize(500,300)
    items.setReadOnly(True)
    grid.addWidget(items,1,0,1,3)

    # populate the textbox
    self.cb_box(default)

    # set window title
    self.setWindowTitle('Playlist')

  def get_info(self):
    """return playlist and shuffled info"""

    # return empty values if nothing is playing or there is no playlist
    xbmc = self.parent().xbmc
    pid = self.parent().xpid()
    if pid is None:
      return {'current':0,'items':[],'shuffled':False}
    params = {'playlistid':pid,'properties':['size']}
    siz = xbmc('Playlist.GetProperties',params)['size']
    if siz==0:
      return {'current':0,'items':[],'shuffled':False}

    # get playlist items
    params = {'playerid':pid,'properties':['position']}
    pos = xbmc('Player.GetProperties',params)['position']+1
    params = {'playlistid':pid,'properties':['title','file','album']}
    items = xbmc('Playlist.GetItems',params)['items']

    # get shuffled state
    params = {'playerid':pid,'properties':['shuffled']}
    shuf = xbmc('Player.GetProperties',params)['shuffled']
    
    return {'current':pos,'items':items,'shuffled':shuf}

  def cb_box(self,i):
    """update textbox when the dropdown menu choice is changed"""

    # get dropdown choice and playlist items
    box = self.sender()
    items = self.get_info()['items']
    choice = self.disp_opts[i]
    lines = []

    # iterate over every playlist item and add info based on the dropdown choice
    for (i,item) in enumerate(items):
      s = str(i+1).zfill(int(math.log(len(items),10))+1)+'&nbsp;'*3+'|'+'&nbsp;'*3
      if choice=='Full Path':
        s += get(item,'file','unknown.xyz')
      if choice=='Filename':
        s += get(item,'label','unknown.xyz')
      if choice=='Title':
        s += get(item,'title','Unknown')
      if choice=='Album - Title':
        s += (get(item,'album','Unknown')+' - '+get(item,'title','Unknown'))
      lines.append(s)

    # update textbox
    self.layout().itemAtPosition(1,0).widget().setHtml('<br>'.join(lines))

################################################################################
# Helper functions                                                             #
################################################################################

def get(x,k,d):
  """get key k from dict d but return default d if it doesn't exist or empty"""
  
  v = x.get(k,d)
  if len(v.strip())==0:
    return d
  return v

def time2sec(t):
  """convert an xbmc time dict to seconds"""
  
  return 3600*t['hours']+60*t['minutes']+t['seconds']

def sec2time(t):
  """convert seconds to an xbmc time dict"""
  
  h = t/3600
  t -= 3600*h
  m = t/60
  t -= 60*m
  return {'hours':h,'minutes':m,'seconds':t}

def time2str(t):
  """convert an xbmc time dict to a string"""
  
  s = ''
  hr = str(t['hours'])
  mi = str(t['minutes'])
  sec = str(t['seconds'])
  if t['hours']>0:
    s += (hr+':')
    s+= (mi.zfill(2)+':')
  else:
    s+= (mi+':')
  s+= sec.zfill(2)
  return s

################################################################################
# Main                                                                         #
################################################################################

def main(args):

  app = QtGui.QApplication(args)
  remote = Remote(args)
  sys.exit(app.exec_())

if __name__ == '__main__':
  main(sys.argv)
