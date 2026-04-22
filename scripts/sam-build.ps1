$env:APPDATA = 'C:\Users\<your-user>\sam-appdata'
$env:PATH = 'C:\Users\<your-user>\AppData\Local\Programs\Python\Python312;' + $env:PATH
& 'C:\Program Files\Amazon\AWSSAMCLI\bin\sam.cmd' build --no-cached
