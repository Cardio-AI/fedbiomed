import logging
import os
import shutil
import subprocess

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logging.basicConfig(level=logging.DEBUG)

class CustomBuildHook(BuildHookInterface):

    def initialize(self, version, build_data):
        # Code in this function will run before building
        super().initialize(version, build_data)

        logger.info("Building node front-end")
        yarn = shutil.which("yarn")

        if yarn is None:
            raise RuntimeError(
                "NodeJS `yarn` is required for building Fed-BioMed front-end application"
            )

        env = os.environ.copy()
        if env.get("REACT_APP_BASE_PATH"):
            logger.info(f"Fedbiomed UI is running with basepath '{os.environ.get('REACT_APP_BASE_PATH')}'")
            env["PUBLIC_URL"] = env["REACT_APP_BASE_PATH"]

        os.chdir("fedbiomed_gui/ui")
        try:
            logger.info("### Yarn: Installation front-end dependencies to prepare build.\n")
            subprocess.run([yarn, "install"], check=True)
            logger.info("\n### Yarn: Building front-end application run.\n")
            subprocess.run([yarn, "build"], check=True, env=env)
        finally:
            os.chdir("../../")

