#  Plznito monitoring

Simple monitoring of cycling tickets in Plzni.to and 
drawing into the web app map running on simple flask server. 

## Installation

The  run it on flask (or flask like) server. 

Requirements
```
Python >= 3.7
python-pip 
git
```

Then installation is simple cloning this repo andi install requirements:
```shell
git clone git@github.com:plzennakole/plznito-monitoring.git
cd plznito-monitoring
pip install -r requirements.txt
```

Update config in `config.json`.

For updating data from plzni.to, you need to run every 6-24hrs. Could be done via adding to cron
```shell
python run_db_update.py
```

For adding to cron open crontab
```shell
crontab -e
```

Append the following entry for updating every midnight or every 6 hrs:
```shell
0 0 * * * /path/to/python run_db_update.py
0 */6 * * * /path/to/python run_db_update.py
```

## Run
```shell
python run_flask.py --config config.json
```

(c) Plzen na kole 2021 
