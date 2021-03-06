# This file is part of pam-accesscontrol.
#
#    Copyright (C) 2017,2018  Alexander Naumov <alexander_naumov@opensuse.org>
#
#    PAM-ACCESSCONTROL is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    PAM-ACCESSCONTROL is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with PAM-ACCESSCONTROL.  If not, see <http://www.gnu.org/licenses/>.

import subprocess as sp
import syslog, os, sys, re, time, datetime, glob, grp, pwd

from ctypes import *
from ctypes.util import find_library

log_prefix = ""


def log(log_message):
  syslog.syslog(log_prefix + str(log_message))


def create_log(SERVICE, rhost, user, mode, msg):
  """
  It creates new entry in the logfile. The format of log-entry is:
  date <SPACE> current time <TAB> service name <TAB> rule <TAB> username@hostname <TAB> some_text <newline>
  """
  now  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  FILE = '/var/log/pam-accesscontrol-' + str(datetime.datetime.now().strftime("%Y-%m")) + '.log'

  if not rhost: rhost = "localhost"
  try:
    fd = open(FILE, 'a+')
    fd.write("%s%s%s%s%s\n" % (now.ljust(23), SERVICE.ljust(10), str(mode).ljust(10), (str(user) + "@" + str(rhost)).ljust(50), msg.ljust(15)))
    fd.close()
  except:
    log("can't open/write logfile " + FILE)


def check_log(SERVICE, rhost, user):
  """
  This funtion can be used to figure out is the current SSH session last on or not.
  In fact we have mulisessions (sessions inside other sessions), but we should be
  notified only about last one.
  """
  FILE = '/var/log/pam-accesscontrol-' + str(datetime.datetime.now().strftime("%Y-%m")) + '.log'

  try:
    fd = open(FILE, 'a+')
    logfile = reversed(fd.read().split("\n"))
    fd.close()
  except:
    log("can't open/read logfile " + FILE)
    return 0

  for l in logfile:
    if len(l) > 0:
      L = [L for L in l.split(" ") if len(L)>0 and re.search('[a-zA-Z]', L)]
      if L[2] == str(user + "@" + rhost):
        if L[4] in ["new", "granted"]:
          log("closing session - user:" + str(user) + " host:"+str(rhost))
          create_log(SERVICE, rhost, user, L[1], "closing session")
          if L[1] == "ASK":
            return 1
          else:
            return 0
  return 0


def ids(LIST):
  return [L for L in LIST.split(",") if len(L)>0]


def not_upper_last_element(config):
  """
  Last rule's element is a username. We won't 'upper' it. Rest should be
  'upper'ed to fix difference between capital and lowercase letters
  (to be able to use both in the config file).
  We also should be carefull with unnecessary spaces that generates excess
  rule's options.
  """
  conf = []
  for line in config:
    if len(line.split(" "))>1:
      line = " ".join([x for x in line.split(" ") if len(x) > 0])
      line = " ".join([x for x in line.upper().split(" ")[:-1]]) + " " + line.split(" ")[-1]
    conf.append(line)
  return conf


def configuration():
  """
  Reading rules list from the config files.
  """
  conf_files = sorted(glob.glob('/etc/pam-accesscontrol.d/*.conf'))
  all_conf = []
  if conf_files:
    for cur_file in conf_files:
      try:
        with open(cur_file, 'r') as fd:
          conf = fd.read().split("\n")
          conf = not_upper_last_element(conf)
          all_conf = all_conf + conf
      except:
        log("can't open file: " + cur_file)
  #log("config: " + str(all_conf))
  return all_conf


def get_default():
  """
  It reads the config file, parse it and tries to find 'DEFAULT' and
  'DEBUG' values.

  DEFAULT:
  This value will be interpreted as a default behavior for the NOT defined
  users or groups. Keep in mind, it supports only two modes CLOSE and OPEN.
  If you define DEFAULT rule many times, it will take value of the last one.
  ATENTION: if DEFAULT rule will be not set in a config file, it will set
  to 'CLOSE' automaticaly.

  DEBUG:
  Same for 'DEBUG'. Default = False and False means that only most important
  events will be logged. Default = True will turn ALL events on. Make sence
  for debugging, but can be confused for users/admins.
  """
  DEBUG   = False
  DEFAULT = 'CLOSE'

  for line in configuration():
    line = line.upper()
    if line[:8] == "DEFAULT:":
      if line.split(":")[1] in ['CLOSE', 'OPEN']:
        #log("default access rule: " + line.split(":")[1])
        DEFAULT = line.split(":")[1]
      else:
        log("default: CLOSE")

    if line[:6] == "DEBUG:":  DEBUG = line.split(":")[1]

  if DEBUG == 'TRUE': DEBUG = True
  else:               DEBUG = False

  if DEBUG: log("default access rule: " + DEFAULT)
  return DEFAULT, DEBUG


def config_parser(SERVICE, DEBUG):
  """
  It reads and parses the config file and returns the LIST of the correctly
  defined rules. Broken rules will be just ignored (for security reason).
  """
  rules = []
  for rule in [c for c in configuration() if len(c) > 5]:
    if DEBUG: log("rule: " + str(rule))
    dic = {}
    if len(rule.split(" ")) != 4:
      if DEBUG: log("broken rule, wrong number of options... skipping: " +str(rule))

    elif rule.split(" ")[0] != SERVICE.upper():
      if DEBUG: log("other service... skipping: " + str(rule))

    elif rule.split(" ")[1] not in ['OPEN', 'CLOSE', 'ASK','NUMBER']:
      if DEBUG: log("second parameter is broken: " +str(rule))

    elif rule.split(" ")[2] not in ['USER', 'GROUP']:
      if DEBUG: log("third parameter is broken: " +str(rule))

    else:
      dic['OPTION'] = str(rule.split(" ")[1] + " " + rule.split(" ")[2])
      dic['LIST'] = ids(rule.split(" ")[3])
      rules.append(dic)
  return rules


def number_of_logged_already(login, group, DEBUG):
  """
  Use this function to figure out number of already logged users which
  belong to the 'group'. Users with the same login (name) are not counted,
  i.e. one user can create n sessions and in this case we still have ONE user.
  It takes 'login' as a parameter to be able to calculate number of users
  after creating this new session.
  """
  item = 0
  USERS = []
  users_in_system = sp.Popen(["/bin/loginctl", "list-users"],stdin=sp.PIPE, stdout=sp.PIPE, stderr=sp.PIPE).communicate()[0].split("\n")
  for users in users_in_system[1:-3]:
    USERS.append([user for user in users.split(" ") if len(user)>0][-1])

  if DEBUG: log("USERS list: " + str(USERS))
  USERS.append(login)
  USERS = dict(zip(USERS, USERS)).values() #delete same users: bob,tom,tom,tom => bob,tom
  if DEBUG: log("USERS list after compression: " + str(USERS))

  for U in USERS:
    if U in check_users_group_list(group, login, DEBUG):
      item = item+1
  if DEBUG: log("number of users (group '" + str(group) + "') after new connection: " + str(item))
  return item


def check_number_in_group(login, LIST, DEBUG):
  """
  It checks LIST of NUMBER rule to make a decision about creating new session.
  """
  allow = []
  for L in LIST:
    if len(L.split(":")) != 2:
      if DEBUG: log("wrong defined rule NUMBER '" + str(L) + "'... skipping")
    else:
      if login in check_users_group_list(L.split(":")[0], login, DEBUG):
        try:
          if int(L.split(":")[1]) < int(number_of_logged_already(login, L.split(":")[0], DEBUG)):
            if DEBUG: log("no more users allowed for group '" + str(L.split(":")[0]) + "'")
            allow.append(False)

          else:
            if DEBUG: log("free place for group " +str(L.split(":")[0]))
            allow.append(True)
        except:
          if DEBUG: log("wrong defined rule NUMBER '" + str(L) + "'. This value should be an integer... skipping")
      else:
        if DEBUG: log("user '" + str(login) + "' is not in group '" + L.split(":")[0] + "'")

  if len(allow) == 0:
    if DEBUG: log("all NUMBER rules have nothing to do with user '" + str(login) + "'")
    return True

  if any(allow): return True
  else:          return False


def check_users_group_list(group, login, DEBUG):
  """
  This function tries to call glibc to get the list of user's groups.
  Theoretically, it should support local host groups, LDAP groups and sssd+LDAP (freeIPA, AD).
  Type of the return value should be a LIST; empty LIST is authorized.
  """
  if group == "ALL":
    if DEBUG: log("okay, group 'ALL' means everyone")
    return [str(login)]

  getgrouplist = cdll.LoadLibrary(find_library('libc')).getgrouplist

  ngroups = 30
  getgrouplist.argtypes = [c_char_p, c_uint, POINTER(c_uint * ngroups), POINTER(c_int)]
  getgrouplist.restype = c_int32

  grouplist = (c_uint * ngroups)()
  ngrouplist = c_int(ngroups)

  user = pwd.getpwnam(login)
  ct = getgrouplist(user.pw_name, user.pw_gid, byref(grouplist), byref(ngrouplist))

  # if 30 groups was not enought this will be -1, try again
  # luckily the last call put the correct number of groups in ngrouplist
  if ct < 0:
    getgrouplist.argtypes = [c_char_p, c_uint, POINTER(c_uint *int(ngrouplist.value)), POINTER(c_int)]
    grouplist = (c_uint * int(ngrouplist.value))()
    ct = getgrouplist(user.pw_name, user.pw_gid, byref(grouplist), byref(ngrouplist))

  for i in xrange(0, ct):
    gid = grouplist[i]
    if (group == grp.getgrgid(gid).gr_name):
      if DEBUG: log("user '" + str(login) + "' is a member of group '" + str(group) + "'")
      return [str(login)]
  return []


def dialog(DEBUG, rhost, user, flavor, SERVICE):
  """
  This calls UserInterface to get confirmations about creating new session.
  It also notified user about session termination.
  """
  return sp.Popen(["/usr/share/pam-accesscontrol/notifications.py",
      str(DEBUG), str(rhost), str(user), flavor, SERVICE], stdin=sp.PIPE, stdout=sp.PIPE, stderr=sp.PIPE).communicate()[0]


def check(access, i, rules, login, DEBUG):
  """
  It gets list of rules, parses it and fills the 'access' dictonary with 4
  lists: CLOSE, ASK, OPEN and NUMBER.
  """
  for r in rules:
    if DEBUG: log("rules: "+ str(r))
    if i['OPTION'].split(" ")[0] == r: #OPEN, ASK, CLOSE, NUMBER
      if DEBUG: log("that was interpreted as " + r +": "+ str(i['OPTION'].split(" ")[0]))

      if i['OPTION'].split(" ")[1] == "USER":
        if DEBUG: log("I'm going to look at USERS list: " +str(i['OPTION'].split(" ")[1]))
        access[r] = access[r] + i['LIST']
 
      elif i['OPTION'].split(" ")[1] == "GROUP":
        if i['OPTION'].split(" ")[0] == "NUMBER":
          if DEBUG: log("I'm going to look at NUMBER of USERS in GROUP: " +str(i['OPTION'].split(" ")[1]))
          access[r] = access[r] + i['LIST']
        else:
          if DEBUG: log("I'm going to look at GROUP list: " +str(i['OPTION'].split(" ")[1]))
          for group in i['LIST']:
            access[r] = access[r] + check_users_group_list(group, login, DEBUG)
  return access


def allow(SERVICE, host, login, DEFAULT, DEBUG):
  access = {"OPEN":[], "ASK":[], "CLOSE":[], "NUMBER":[]}
  ret = None

  for rule in config_parser(SERVICE, DEBUG):
    if DEBUG:
      log("----------------------------------------------")
      log("rule = " +str(rule))
    access.update(check(access, rule, ["OPEN", "ASK", "CLOSE", "NUMBER"], login, DEBUG))

  if DEBUG:
    log("----------------------------------------------")
    log("OPEN for  : "+str(access['OPEN']))
    log("CLOSE for : "+str(access['CLOSE']))
    log("ASK for   : "+str(access['ASK']))
    log("NUMBER for: "+str(access['NUMBER']))

  if len(access['NUMBER']) > 0:
    if not check_number_in_group(login, access['NUMBER'], DEBUG):
      if DEBUG: log("'allow()' returns 'CLOSE', because of access[NUMBER]")
      return "CLOSE"

  # Priority of CLOSE rule is higher than OPEN
  for i in access['OPEN']:
    if i in access['CLOSE']: access['OPEN'].remove(i)

  if login in access['CLOSE']:          return "CLOSE"
  elif login in access['ASK']:
    if SERVICE in ["sshd", "sshd-key"]: return "ASK"
    else:                               return "CLOSE"
  elif login in access['OPEN']:         return "OPEN"
  else:                                 return DEFAULT


def main(SERVICE, pamh, flags, argv):
  """
  Start point for creating new sessions. It asks function 'allow'
  to define next steps. Function 'main' uses PAM object 'pamh' and
  its methods to define name of the remote host and user's name.
  """

  DEFAULT, DEBUG = get_default()
  log("DEBUG is set to " + str(DEBUG))

  try:
    user = pamh.get_user()
    rhost = pamh.rhost
  except pamh.exception, e:
    log("something goes wrong... no info about remote connection")
    return e.pam_result

  mode = allow(SERVICE, rhost, user, DEFAULT, DEBUG)
  if DEBUG: log("main got from allow: "+str(mode))

  if mode == "ASK":
    if DEBUG: log("SHOW ME WINDOW")
    ret = str(dialog(DEBUG, rhost, user, "ask", SERVICE))
    if DEBUG: log("[0->Yes; 1->No] RET = " + str(ret))
    try:
      if int(ret) == 0:
        if allow(SERVICE, rhost, user, DEFAULT, False) == "ASK":
          create_log(SERVICE, rhost, user, mode, "creating new session")
          log("access granted")
          return pamh.PAM_SUCCESS
        else:
          log("connection CAN NOT be established; because of NUMBER rule")
          return pamh.PAM_AUTH_ERR
      else:
        log("connection SHOULD NOT be established; because of X-session owner's decision")
        return pamh.PAM_AUTH_ERR
    except:
      log("something goes wrong... no return value from notification window")
      return pamh.PAM_AUTH_ERR

  elif mode == "CLOSE":
    create_log(SERVICE, rhost, user, mode, "access denied")
    log("access denied")
    if str(pamh.service) in ["slim","sddm","lightdm","xdm","kdm"]:
      dialog(DEBUG, rhost, user, "xorg", SERVICE)
    return pamh.PAM_AUTH_ERR

  elif mode == "OPEN":
    create_log( SERVICE, rhost, user, mode, "access granted")
    log("access granted")
    return pamh.PAM_SUCCESS

  else:
    log("I don't know what to do now... " +str(mode))
    return pamh.PAM_AUTH_ERR


def pam_sm_authenticate(pamh, flags, argv):
  global log_prefix
  log_prefix = "pam-accesscontrol(" + str(pamh.service) + ":" + str(pamh.get_user()) +"): "

  log("==============================================")
  log("authentication")

  try:
    log("remote user: "+ str(pamh.get_user()))
    log("remote host: "+ str(pamh.rhost))
  except pamh.exception, e:
    log("something goes wrong... no info about remote connection")
    return pamh.PAM_AUTH_ERR

  if str(pamh.service) == "sshd":
    try:
      os.mknod("/tmp/session-" + str(pamh.pamh))
    except:
      log("can't create new file in /tmp...")

  return main(str(pamh.service), pamh, flags, argv)


def pam_sm_close_session(pamh, flags, argv):
  global log_prefix
  log_prefix = "pam-accesscontrol(" + str(pamh.service) + ":" + str(pamh.get_user()) +"): "

  log("closing session")

  if not check_log("sshd", str(pamh.rhost), str(pamh.get_user())):
    log("no need to notify")
  else:
    DEFAULT, DEBUG = get_default()
    if DEBUG: log("SHOW ME WINDOW")
    dialog(DEBUG, str(pamh.rhost), str(pamh.get_user()), "info", str(pamh.service))

  return pamh.PAM_SUCCESS


def pam_sm_open_session(pamh, flags, argv):
  global log_prefix
  log_prefix = "pam-accesscontrol(" + str(pamh.service) + ":" + str(pamh.get_user()) +"): "

  log("==============================================")
  log("open new session")

  if str(pamh.service) == "sshd":
    if os.path.isfile("/tmp/session-" + str(pamh.pamh)):
      SERVICE = "sshd"
      os.remove("/tmp/session-" + str(pamh.pamh))
    else:
      SERVICE = "sshd-key"
    log(SERVICE)
    return main(SERVICE, pamh, flags, argv)

  elif str(pamh.service) in ["slim","sddm","lightdm","xdm","kdm"]:
    # We check XDM's rules on the 'auth' step.
    # (because we want to show error message (in CLOSE case)
    # and it's possible only BEFORE KDE-session starts)
    log("open session")
    return pamh.PAM_SUCCESS

  else:
    return main(str(pamh.service), pamh, flags, argv)


def pam_sm_setcred(pamh, flags, argv):
  return pamh.PAM_SUCCESS
