#!/bin/bash

RELATIVEDIR=`echo $0|sed s/run.sh//g`
cd $RELATIVEDIR

chmod +x ./gtk_zpl_viewer.py
python3 -u ./gtk_zpl_viewer.py
