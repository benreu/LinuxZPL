#!/bin/bash

RELATIVEDIR=`echo $0|sed s/run.sh//g`
cd $RELATIVEDIR

chmod +x ./linuxzpl.py
python3 -u ./linuxzpl.py "$@"
