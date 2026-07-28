# Copyright 2026 Max Planck Institute for Software Systems,
# National University of Singapore, and SimBricks UG (haftungsbeschränkt)
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

from __future__ import annotations

import math
import pathlib
import shutil
import typing_extensions as tpe

from simbricks.orchestration import system
from simbricks.orchestration.instantiation import base as inst_base
from simbricks.orchestration.simulation import base as sim_base
from simbricks.orchestration.simulation import host as sim_host
from simbricks.orchestration.system import host as sys_host
from simbricks.orchestration.system import pcie as sys_pcie
from simbricks.orchestration.system import nic as sys_nic
from simbricks.orchestration.system import disk_images
from simbricks.orchestration.instantiation import socket as inst_socket
from simbricks.utils import base as utils_base
from simbricks.utils import eth as utils_eth


class QemuSim(sim_host.HostSim):

    def __init__(self, simulation: sim_base.Simulation) -> None:
        super().__init__(
            simulation=simulation,
            executable="qemu-system-x86_64",
        )
        self.name = f"QemuSim-{self._id}"
        self.kernel_path = "global_input/images/bzImage"
        self.initrd: str | None = None
        self._qemu_img_exec: str = "qemu-img"

    def resreq_cores(self) -> int:
        return 1

    def resreq_mem(self) -> int:
        return 1024

    def _check_nic_and_host_specs(self) -> None:
        full_sys_hosts = self.filter_components_by_type(ty=sys_host.BaseLinuxHost)
        nic_spes = self.filter_components_by_type(ty=sys_nic.SimplePCIeNIC)
        if len(full_sys_hosts) != 1 and len(nic_spes) > 1:
            raise RuntimeError(
                "QEMU only supports simulating a single NIC together with its connected host"
            )

    def supported_socket_types(self, interface: system.Interface) -> set[inst_socket.SockType]:
        self._check_nic_and_host_specs()
        if len(self.filter_components_by_type(ty=sys_nic.SimplePCIeNIC)) > 0:
            return {inst_socket.SockType.LISTEN}
        else:
            return {inst_socket.SockType.CONNECT}

    def add(self, host_or_nic: sys_host.Host | sys_nic.SimplePCIeNIC):
        match host_or_nic:
            case sys_nic.SimplePCIeNIC() | sys_host.Host():
                super().add(host_or_nic)
            case _:
                raise Exception(
                    f"QEMU does not support simulating component with type {type(host_or_nic)}"
                )

    def supported_image_formats(self) -> list[str]:
        return ["qcow2", "raw"]

    def toJSON(self) -> dict:
        json_obj = super().toJSON()
        # disks is created upon invocation of "prepare", hence we do not need to serialize it
        json_obj["kernel_path"] = self.kernel_path
        json_obj["initrd"] = self.initrd
        json_obj["qemu_img_exec"] = self._qemu_img_exec
        return json_obj

    @classmethod
    def fromJSON(cls, simulation: sim_base.Simulation, json_obj: dict) -> tpe.Self:
        instance = super().fromJSON(simulation, json_obj)
        instance.kernel_path = utils_base.get_json_attr_top(json_obj, "kernel_path")
        instance.initrd = utils_base.get_json_attr_top(json_obj, "initrd") 
        instance._qemu_img_exec = utils_base.get_json_attr_top(json_obj, "qemu_img_exec")
        return instance

    async def _make_qcow_copy(
        self, inst: inst_base.Instantiation, disk: disk_images.DiskImage, format: str, ident: str
    ) -> str:
        disk_path = pathlib.Path(disk.path(inst=inst, format=format))
        copy_path = inst.env.img_dir(relative_path=f"hdcopy.{self._id}.{ident}")
        prep_cmds = [
            (
                f"{self._qemu_img_exec} create -f qcow2 -F qcow2 "
                f'-o backing_file="{disk_path}" '
                f"{copy_path}"
            )
        ]
        await inst._cmd_executor.exec_simulator_prepare_cmds(self, prep_cmds)
        return copy_path

    async def _make_raw_copy(
        self, inst: inst_base.Instantiation, disk: disk_images.DiskImage, format: str, ident: str
    ) -> str:
        disk_path = pathlib.Path(disk.path(inst=inst, format=format))
        copy_path = inst.env.img_dir(relative_path=f"hdcopy.{self._id}.{ident}")
        shutil.copy2(disk_path, copy_path)
        return copy_path

    async def copy_disk_image(
        self, inst: inst_base.Instantiation, disk_image: disk_images.DiskImage, ident: str
    ) -> str:
        format = disk_image.find_format(self)
        if format == "qcow2":
            return await self._make_qcow_copy(inst, disk_image, format, ident)
        else:
            return await self._make_raw_copy(inst, disk_image, format, ident)

    def checkpoint_commands(self) -> list[str]:
        return []

    def cleanup_commands(self) -> list[str]:
        return ["poweroff -f"]

    def run_cmd(self, inst: inst_base.Instantiation) -> str:

        if len(self.get_channels()) == 0:
            sync = False
        else:
            latency, period, sync = sim_base.Simulator.get_unique_latency_period_sync(
                channels=self.get_channels()
            )

        accel = ",accel=kvm:tcg" if not sync else ""

        cmd = (
            f"{self._executable} -machine q35{accel} -serial mon:stdio "
            "-cpu Skylake-Server -display none -nic none "
            f"-kernel {inst.env.work_dir_or_abs(self.kernel_path, True)} "
        )

        if self.initrd is not None:
            cmd += f" -initrd {inst.env.work_dir_or_abs(self.initrd, True)} "

        full_sys_hosts = self.filter_components_by_type(ty=sys_host.BaseLinuxHost)
        if len(full_sys_hosts) != 1:
            raise Exception("QEMU only supports simulating 1 FullSystemHost")
        host_spec = full_sys_hosts[0]

        kcmd_append = ""
        if host_spec.kcmd_append is not None:
            kcmd_append = " " + host_spec.kcmd_append

        assert host_spec in self._disk_images
        for index, disk in enumerate(self._disk_images[host_spec]):
            format = disk[0].find_format(self)
            cmd += f"-drive file={disk[1]},if=ide,index={index},media=disk,driver={format} "
        cmd += (
            '-append "earlyprintk=ttyS0 console=ttyS0 root=/dev/sda1 '
            f'init=/home/ubuntu/guestinit.sh rw{kcmd_append}" '
            f"-m {host_spec.memory} -smp {host_spec.cores} "
        )

        if sync:
            unit = host_spec.cpu_freq[-3:]
            if unit.lower() == "ghz":
                base = 0
            elif unit.lower() == "mhz":
                base = 3
            else:
                raise ValueError("cpu frequency specified in unsupported unit")
            num = float(host_spec.cpu_freq[:-3])
            shift = base - int(math.ceil(math.log(num, 2)))

            cmd += f" -icount shift={shift},sleep=off "

        self._check_nic_and_host_specs()

        nic_spes = self.filter_components_by_type(ty=sys_nic.SimplePCIeNIC)
        if len(nic_spes) == 1:
            """
            simulate host + NIC -> ethernet adapter
            """
            nic_spec = nic_spes[0]

            # TODO: assert that NIC and host spec are directly connected

            socket = inst.get_socket(interface=nic_spec._eth_if)
            assert socket is not None and socket._type == inst_socket.SockType.LISTEN
            params_url = self.get_parameters_url(
                inst, socket, sync=sync, latency=latency, sync_period=period
            )

            nic_cmd = ""
            match nic_spec:
                case sys_nic.VirtIONic():
                    nic_cmd = "virtio-net-pci,ioeventfd=off"
                case _:
                    raise RuntimeError(
                        f"Currently only NICs of type VirtIONic are supported. You passed: {type(nic_spec)}"
                    )

            # TODO: put MAC addresses in specification objects
            mac = utils_eth.random_mac()

            cmd += f"-device {nic_cmd},netdev=simnet0,mac={mac} -netdev simbricks-eth,id=simnet0,sock-path={params_url} "

            # if we simulate a host with its NIC in a single instance, we do not expose the pci interface below
            return cmd

        else:
            """
            simulate host only -> pcie adapter
            """
            fsh_interfaces = host_spec.interfaces()
            pci_interfaces = system.Interface.filter_by_type(
                interfaces=fsh_interfaces, ty=sys_pcie.PCIeHostInterface
            )
            for inf in pci_interfaces:
                assert len(self.get_channels()) > 0
                socket = inst.get_socket(interface=inf)
                if socket is None:
                    continue
                assert socket._type is inst_socket.SockType.CONNECT
                cmd += f"-device simbricks-pci,socket={socket._path}"
                if sync:
                    cmd += ",sync=on"
                    cmd += f",pci-latency={latency}"
                    cmd += f",sync-period={period}"
                else:
                    cmd += ",sync=off"
                cmd += " "

            return cmd
