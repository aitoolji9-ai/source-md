from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class MacOSWheelHook(BuildHookInterface):
    def initialize(self, version, build_data):
        build_data['pure_python']=False
        build_data['infer_tag']=False
        build_data['tag']='py3-none-macosx_11_0_universal2'
