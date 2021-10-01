#  Plznito monitoring

Simple monitoring of cycling tickets in Plzni.to. 

## Installation

You need to run it on flask (or flask like) server. Requirements
```
Python >= 3.7
pip 
git
```

Then installation is simple cloning this repo andi install requirements:
```shell
git clone
pip install -r requirements.txt
```

Update config in `config.json`.

For updating data from plzni.to, you need to run every 6-24hrs. Could be done via adding to cron
```shell
python run_db_update.py
```

## Run
```shell
python run_flask.py --config config.json
```



(c) Plzen na kole 2021 
