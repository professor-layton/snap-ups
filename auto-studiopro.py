import time
from pywinauto import Desktop
from pywinauto import Application
from pywinauto.keyboard import send_keys

Application().start('C:\\Program Files\\Mendix\\10.6.12.43243\\modeler\\studiopro.exe')
# Application().connect(...): process, handle, path
appUIA = Application(backend="uia").connect(path='C:\\Program Files\\Mendix\\10.6.12.43243\\modeler\\studiopro.exe')
appWIN = Application(backend="win32").connect(path='C:\\Program Files\\Mendix\\10.6.12.43243\\modeler\\studiopro.exe')
time.sleep(16)

'''
# how to refer to the window of given control element
# Method 1:
# app[<window class name>] or app[<window title name>]
# Method 2:
# app.<window class name>
'''

winUIA = appUIA.window(class_name="Window", title="Mendix Studio Pro - Sign In")
# control.print_control_identifiers(depth=None, filename=None)
# ...
# Pane - 'XXX' (L192, T104, R1728, B936) # list coordinates of each UI element
# ...
winUIA.set_focus()
btn = winUIA.child_window(title="Go to Web Sign In", auto_id="GotoWebSignIn", control_type="Button")
btn.click_input()
time.sleep(16)

crm = Desktop(backend="uia").window(title="Private Mendix Platform - Login - Google Chrome")
# pywinauto can't extract elements of some chrome webpage properly (no child window recognized)
# it's impossible or quite complicated to extract 'Username' and 'Password' UI element hierarchy
# use this tricky workaround below
# NOTE: 'Username' textbox will always get focus when sign in webpage gets displayed completely
if crm.exists():
    crm.set_focus()
    send_keys('weiqiang{TAB}Abc123456.{TAB}{TAB}{ENTER}')
    time.sleep(16)

winUIA = appUIA.window(class_name="Window", title="Mendix Studio Pro - Select App")
winUIA.set_focus()

btn = winUIA.child_window(title="Open App Locally", auto_id="OpenAppLocally", control_type="Button")
btn.click_input()

winWIN = appWIN.window(title="Open App", class_name="#32770")
txt = winWIN.child_window(class_name="Edit")
txt.set_edit_text("C:\\fortify_latest\\PANDA-4147\\bk\\fortify\\Mendix fortify")
btn = winWIN.child_window(title="&Open", class_name="Button")
btn.click_input()
time.sleep(32)

winUIA = appUIA.window(class_name="Window", auto_id="StudioProMainWindow")
winUIA.maximize()
# exception occurs when all tabs are initially closed and "File->Close All" menu is disabled
try:
    winUIA.menu_select("File->Close All")
except Exception:
    pass

# marketplace connecting
winUIA.menu_select("View->Marketplace")
tab = winUIA.child_window(title="Marketplace", control_type="TabItem")
time.sleep(16) # wait for connecting

# component searching
txt = tab.child_window(class_name="form-control", control_type="Edit")
txt.set_edit_text("Commons") # component keywords
txt.set_focus()
txt.click_input()
send_keys('{ENTER}')
time.sleep(16) # wait for searching

# open component download page
btn = tab.child_window(control_type='Button', title_re='Community Commons*')
tab.set_focus()
btn.click_input()

'''
# close marketplace searching tab
txt.set_focus()
txt.click_input()
send_keys('+{ESC}') # SHIFT + ESC, https://pywinauto.readthedocs.io/en/latest/code/pywinauto.keyboard.html
'''

# locate to opened download page
tab = winUIA.child_window(title="Marketplace", control_type="TabItem", found_index=0)
time.sleep(32) # wait for opening
btn = tab.child_window(title="Download", control_type="Button")
tab.set_focus()
btn.click_input()
time.sleep(8) # wait for downloading

# 'Import Module' popup
pup = winUIA.child_window(title="Import Module", class_name="Window")
btn = pup.child_window(title="Import", auto_id="Import", control_type="Button")
btn.click_input()
time.sleep(16)

# 'Information' popup
pup = winUIA.child_window(title="Information", class_name="Window")
btn = pup.child_window(title="OK", auto_id="OK", control_type="Button")
btn.click_input()

winUIA.menu_select("Run->Run Locally")
time.sleep(256)
