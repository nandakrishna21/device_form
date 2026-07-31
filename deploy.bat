@echo off
echo Copying files to live site...
xcopy /y /e "C:\Users\Administrator\device_form\templates" "C:\inetpub\wwwroot\device_form\templates"
xcopy /y /e "C:\Users\Administrator\device_form\templates" "C:\Users\Administrator\Desktop\device_form\templates"
copy /y "C:\Users\Administrator\device_form\*.html" "C:\inetpub\wwwroot\device_form"
copy /y "C:\Users\Administrator\device_form\*.html" "C:\Users\Administrator\Desktop\device_form"
copy /y "C:\Users\Administrator\device_form\app.py" "C:\inetpub\wwwroot\device_form\app.py"
copy /y "C:\Users\Administrator\device_form\app.py" "C:\Users\Administrator\Desktop\device_form\app.py"
echo Restarting IIS...
iisreset /noforce
echo Done! Changes are live at https://win.howtostart.in/form
