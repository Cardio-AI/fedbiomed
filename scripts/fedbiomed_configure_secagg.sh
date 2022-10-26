#!/bin/bash

### Configuration


# Color configuration -----------------------------------------------------------------------------------------
RED='\033[1;31m' #red
YLW='\033[1;33m' #yellow
GRN='\033[1;32m' #green
NC='\033[0m' #no color
BOLD='\033[1m'

# Base Fed-BioMed directory ------------------------------------------------------------------------------------
[[ -n "$ZSH_NAME" ]] || myname=${BASH_SOURCE[0]}
[[ -n "$ZSH_NAME" ]] && myname=${(%):-%x}
basedir=$(cd $(dirname $myname)/.. || exit ; pwd)

# MP-SPDZ git submodule directory
mpsdpz_basedir=$basedir/modules/MP-SPDZ
# ---------------------------------------------------------------------------------------------------------------


echo -e "\n${GRN}Starting MP-SPDZ configuration...${NC}"
# Clone initialize github submodule if it is not existing
if [ -d "$mpsdpz_basedir" ]; then
  git submodule update --init modules/MP-SPDZ
fi

# Get system information  ---------------------------------------------------------------------------------------
echo -e "\n${YLW}--------------------------------SYSTEM INFORMATION------------------------------------------${NC}"
if test $(uname) = "Linux"; then
  echo -e "${BOLD}Linux detected. MP-SPDZ will be used through binary distribution${NC}\n"
  cpu_info='cat /proc/cpuinfo'
elif test $(uname) = "Darwin"; then
  echo -e "${BOLD}macOS detected. MP-SPDZ will be compiled from source instead of using binary distribution${NC}\n"
else
  echo -e "${RED}ERROR${NC}: Unknown operation system. Only Linux or macOS based operating systems are supported\n"
  echo -e "Aborting installation \n"
  exit 1
fi
# ----------------------------------------------------------------------------------------------------------------


# Detect architecture
if test "$cpu_info"; then
  echo -e "${YLW}--------------------------------ARCHITECTURE INFO-------------------------------------------${NC}"
  if $cpu_info | grep -q avx2; then
    echo -e "${BOLD}CPU uses Advanced Vector Extension 2 'avx2'${NC}\n"
  elif $cpu_info | grep -q avx2; then
    echo -e "${BOLD}CPU uses Advanced Micro Devices 64 'amd64'${NC}\n"
  else
    echo -e "${RED}ERROR${NC}: Unknown CPU architecture"
    exit 1
  fi
fi

echo "$mpsdpz_basedir"
