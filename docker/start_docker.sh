#!/usr/bin/env bash

exec docker run --privileged -it -v ~/work:/root/work -p 4711:6006 robothor:latest
