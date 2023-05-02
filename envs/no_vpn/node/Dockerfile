FROM python:3.9-slim

# RUN useradd node

# WORKDIR /home/node

RUN apt-get update && apt-get install -y git build-essential curl
RUN apt-get update && apt-get install -y apt-utils wget
RUN apt-get -y install rsync

RUN wget -q --directory-prefix=$HOME https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh && \
        bash $HOME/Miniconda3-latest-Linux-x86_64.sh -b -p /miniconda && \
        rm -f $HOME/Miniconda3-latest-Linux-x86_64.sh

RUN mkdir /fedbiomed
WORKDIR /fedbiomed
RUN mkdir -p /fedbiomed/envs /data
COPY fedbiomed /fedbiomed/fedbiomed
COPY scripts /fedbiomed/scripts
COPY envs/development/conda/fedbiomed-node.yaml /fedbiomed/envs/
# COPY envs/no_vpn/node/requirements.txt .

# RUN python -m venv venv
# RUN venv/bin/pip install --no-cache-dir -r requirements.txt

ENV PATH=${PATH}:/miniconda/bin
ENV PYTHONPATH=/home/node/fedbiomed

RUN conda env update --file ./envs/fedbiomed-node.yaml && \
	pip cache purge && conda clean -a -y

COPY envs/no_vpn/node/entrypoint.sh .
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
