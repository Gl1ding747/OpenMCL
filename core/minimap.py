import struct
import zlib
import os
import math
from collections import OrderedDict

import ctypes
import ctypes.wintypes as wintypes

# numpy 可让内存扫描向量化(跑在 C 层、释放 GIL)，4 个扫描线程真正并行，
# 扫描本身快数十倍、CPU 占用大降 —— 这是“卡”真正的瓶颈所在。
try:
    import numpy as np
    _HAS_NUMPY = True
except Exception:
    np = None
    _HAS_NUMPY = False

PROCESS_VM_READ = 0x0010


def _scan_candidate_regions(regions, budget=2 * 1024 * 1024 * 1024):
    """选出门小地图扫描的候选内存区。

    JVM 堆是一大块已提交的 MAPPED/PRIVATE 内存，且常按块提交而碎片化
    （可能切成几 MB ~ 几十 MB 的小块），阈值过高会漏掉整片堆导致 R:0。
    budget 定义的是本轮可轮转覆盖的“跨度范围”，并非单周期要扫字节数：
    实际每周期只扫一个 WINDOW 窗口，靠轮转推进覆盖整个跨度。跨度越大，
    越能轮转到堆深处的活对象区，但单周期 CPU 保持不变。
    """
    if not regions:
        return []
    heap = [r for r in regions if r["type"] == "HEAP"]
    if heap:
        heap.sort(key=lambda r: r["size"], reverse=True)
        return _cap_budget(heap, budget)
    candidates = [r for r in regions
                  if r["type"] in ("MAPPED", "PRIVATE")
                  and r["size"] >= 4 * 1024 * 1024]
    candidates.sort(key=lambda r: r["size"], reverse=True)
    chosen = _cap_budget(candidates, budget)
    if not chosen:
        # 兜底：仍无候选则取所有已提交、可读的最大区域，保证不会 R:0。
        fallback = [r for r in regions
                    if r["type"] not in ("CODE", "IMAGE")
                    and r["size"] >= 1 * 1024 * 1024]
        chosen = _cap_budget(fallback, budget)
    return chosen


def _cap_budget(items, budget):
    chosen = []
    used = 0
    for r in items:
        sz = r["size"]
        if used + sz > budget:
            sz = budget - used
        if sz <= 0:
            break
        item = dict(r)
        item["size"] = sz
        chosen.append(item)
        used += sz
    return chosen


def detect_active_world(pid, saves_dir):
    if not os.path.isdir(saves_dir):
        return None
    world_names = [
        d for d in os.listdir(saves_dir)
        if os.path.isdir(os.path.join(saves_dir, d))
        and os.path.isdir(os.path.join(saves_dir, d, "region"))
    ]
    if not world_names:
        return None
    name_patterns = {name: name.encode("utf-8") for name in world_names}
    max_len = max(len(p) for p in name_patterns.values())
    from .stealth_memory import stealth_read_process_memory as sread, query_memory_regions
    try:
        regions = query_memory_regions(pid)
    except Exception:
        return None
    for region in _scan_candidate_regions(regions, budget=256 * 1024 * 1024):
        if region["size"] > 16 * 1024 * 1024:
            region["size"] = 16 * 1024 * 1024
        addr = region["base"]
        remaining = region["size"]
        overlap = min(max_len, 4096)
        while remaining > overlap:
            read_size = min(1048576, remaining)
            try:
                buf = sread(pid, addr, read_size)
            except Exception:
                addr += read_size
                remaining -= read_size
                continue
            for name, pattern in name_patterns.items():
                if pattern in buf:
                    return name
            # 覆盖最后 overlap 字节，确保名字恰好落在读取分块边界时也不会漏检。
            step = read_size - overlap
            addr += step
            remaining -= step
    return None


TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

TAG_NAMES = {
    0: "END", 1: "BYTE", 2: "SHORT", 3: "INT", 4: "LONG",
    5: "FLOAT", 6: "DOUBLE", 7: "BYTE_ARRAY", 8: "STRING",
    9: "LIST", 10: "COMPOUND", 11: "INT_ARRAY", 12: "LONG_ARRAY",
}


class NBTParseError(Exception):
    pass


class NBTReader:

    def __init__(self, data):
        self._data = data
        self._pos = 0

    def _read(self, n):
        if self._pos + n > len(self._data):
            raise NBTParseError("Unexpected EOF at {}".format(self._pos))
        chunk = self._data[self._pos:self._pos + n]
        self._pos += n
        return chunk

    def _read_ubyte(self):
        return self._read(1)[0]

    def _read_byte(self):
        return struct.unpack(">b", self._read(1))[0]

    def _read_ushort(self):
        return struct.unpack(">H", self._read(2))[0]

    def _read_int(self):
        return struct.unpack(">i", self._read(4))[0]

    def _read_long(self):
        return struct.unpack(">q", self._read(8))[0]

    def _read_float(self):
        return struct.unpack(">f", self._read(4))[0]

    def _read_double(self):
        return struct.unpack(">d", self._read(8))[0]

    def _read_string(self):
        length = self._read_ushort()
        return self._read(length).decode("utf-8", errors="replace")

    def _read_byte_array(self):
        length = self._read_int()
        return self._read(length)

    def _read_int_array(self):
        length = self._read_int()
        return [self._read_int() for _ in range(length)]

    def _read_long_array(self):
        length = self._read_int()
        return [self._read_long() for _ in range(length)]

    def _read_list(self):
        elem_type = self._read_ubyte()
        length = self._read_int()
        items = [self._read_payload(elem_type) for _ in range(length)]
        return items, elem_type

    def _read_compound(self):
        result = OrderedDict()
        while True:
            tag = self._read_ubyte()
            if tag == TAG_END:
                return result
            name = self._read_string()
            result[name] = self._read_payload(tag)
        return result

    def _read_payload(self, tag):
        if tag == TAG_BYTE:
            return self._read_byte()
        elif tag == TAG_SHORT:
            return self._read_ushort()
        elif tag == TAG_INT:
            return self._read_int()
        elif tag == TAG_LONG:
            return self._read_long()
        elif tag == TAG_FLOAT:
            return self._read_float()
        elif tag == TAG_DOUBLE:
            return self._read_double()
        elif tag == TAG_BYTE_ARRAY:
            return self._read_byte_array()
        elif tag == TAG_STRING:
            return self._read_string()
        elif tag == TAG_LIST:
            items, _et = self._read_list()
            return items
        elif tag == TAG_COMPOUND:
            return self._read_compound()
        elif tag == TAG_INT_ARRAY:
            return self._read_int_array()
        elif tag == TAG_LONG_ARRAY:
            return self._read_long_array()
        else:
            raise NBTParseError("Unknown tag: {}".format(tag))

    def read_root(self):
        tag = self._read_ubyte()
        if tag != TAG_COMPOUND:
            raise NBTParseError("Root must be TAG_Compound, got {}".format(tag))
        name = self._read_string()
        return self._read_compound()


def parse_nbt(data):
    return NBTReader(data).read_root()


BLOCK_COLORS = {
    "minecraft:grass_block": (0x4A, 0x80, 0x30),
    "minecraft:dirt": (0x87, 0x61, 0x38),
    "minecraft:stone": (0x7F, 0x7F, 0x7F),
    "minecraft:deepslate": (0x50, 0x50, 0x58),
    "minecraft:water": (0x20, 0x50, 0xD0),
    "minecraft:sand": (0xDB, 0xD0, 0x82),
    "minecraft:sandstone": (0xDA, 0xD0, 0x7A),
    "minecraft:gravel": (0x80, 0x7A, 0x70),
    "minecraft:clay": (0x9B, 0xA4, 0xB4),
    "minecraft:bedrock": (0x33, 0x33, 0x33),
    "minecraft:obsidian": (0x15, 0x0E, 0x24),
    "minecraft:diamond_ore": (0x96, 0xBC, 0xC4),
    "minecraft:iron_ore": (0xBB, 0xA0, 0x86),
    "minecraft:gold_ore": (0xCC, 0xB0, 0x50),
    "minecraft:coal_ore": (0x4A, 0x4A, 0x4A),
    "minecraft:redstone_ore": (0xCC, 0x44, 0x44),
    "minecraft:lapis_ore": (0x3A, 0x48, 0xB0),
    "minecraft:emerald_ore": (0x20, 0x8C, 0x3C),
    "minecraft:copper_ore": (0xCC, 0x88, 0x60),
    "minecraft:oak_log": (0x7C, 0x60, 0x3C),
    "minecraft:spruce_log": (0x5C, 0x3C, 0x20),
    "minecraft:birch_log": (0xC8, 0xCC, 0xA8),
    "minecraft:jungle_log": (0x7C, 0x5C, 0x38),
    "minecraft:acacia_log": (0x80, 0x64, 0x44),
    "minecraft:dark_oak_log": (0x44, 0x30, 0x18),
    "minecraft:mangrove_log": (0x68, 0x30, 0x20),
    "minecraft:cherry_log": (0xC0, 0x88, 0x98),
    "minecraft:oak_leaves": (0x2C, 0x72, 0x0C),
    "minecraft:spruce_leaves": (0x1C, 0x4C, 0x12),
    "minecraft:birch_leaves": (0x68, 0x88, 0x52),
    "minecraft:jungle_leaves": (0x2C, 0x8C, 0x18),
    "minecraft:acacia_leaves": (0x44, 0x88, 0x10),
    "minecraft:dark_oak_leaves": (0x44, 0x88, 0x10),
    "minecraft:grass": (0x48, 0x94, 0x2C),
    "minecraft:tall_grass": (0x48, 0x94, 0x2C),
    "minecraft:fern": (0x3C, 0x78, 0x1C),
    "minecraft:snow": (0xFA, 0xFA, 0xFA),
    "minecraft:snow_block": (0xF0, 0xF4, 0xF8),
    "minecraft:ice": (0x90, 0xC0, 0xF0),
    "minecraft:packed_ice": (0x70, 0xA0, 0xE0),
    "minecraft:blue_ice": (0x50, 0x80, 0xD0),
    "minecraft:lava": (0xD0, 0x60, 0x20),
    "minecraft:magma_block": (0x90, 0x30, 0x10),
    "minecraft:netherrack": (0x80, 0x2C, 0x30),
    "minecraft:crimson_stem": (0x6C, 0x20, 0x30),
    "minecraft:warped_stem": (0x20, 0x54, 0x5C),
    "minecraft:nether_wart_block": (0x80, 0x1C, 0x1C),
    "minecraft:warped_wart_block": (0x14, 0x8A, 0x87),
    "minecraft:end_stone": (0xD8, 0xD4, 0x90),
    "minecraft:cobblestone": (0x7A, 0x7A, 0x7A),
    "minecraft:mossy_cobblestone": (0x5B, 0x71, 0x43),
    "minecraft:farmland": (0x6E, 0x50, 0x2C),
    "minecraft:coarse_dirt": (0x6E, 0x50, 0x2C),
    "minecraft:podzol": (0x58, 0x40, 0x18),
    "minecraft:mycelium": (0x6C, 0x5C, 0x6C),
    "minecraft:terracotta": (0x9A, 0x62, 0x44),
    "minecraft:white_terracotta": (0xC8, 0xB8, 0xAC),
    "minecraft:orange_terracotta": (0xA0, 0x60, 0x2C),
    "minecraft:magenta_terracotta": (0x94, 0x4C, 0x50),
    "minecraft:light_blue_terracotta": (0x6C, 0x84, 0x98),
    "minecraft:yellow_terracotta": (0xB4, 0x84, 0x28),
    "minecraft:lime_terracotta": (0x64, 0x70, 0x38),
    "minecraft:pink_terracotta": (0xA0, 0x64, 0x50),
    "minecraft:gray_terracotta": (0x44, 0x38, 0x2A),
    "minecraft:light_gray_terracotta": (0x88, 0x7A, 0x68),
    "minecraft:cyan_terracotta": (0x4E, 0x5A, 0x5A),
    "minecraft:purple_terracotta": (0x72, 0x40, 0x60),
    "minecraft:blue_terracotta": (0x4C, 0x40, 0x64),
    "minecraft:brown_terracotta": (0x50, 0x38, 0x2C),
    "minecraft:green_terracotta": (0x4C, 0x50, 0x2C),
    "minecraft:red_terracotta": (0x8C, 0x3C, 0x30),
    "minecraft:black_terracotta": (0x24, 0x1C, 0x18),
    "minecraft:white_concrete": (0xD0, 0xD4, 0xD4),
    "minecraft:orange_concrete": (0xE0, 0x60, 0x10),
    "minecraft:magenta_concrete": (0xA4, 0x30, 0x88),
    "minecraft:light_blue_concrete": (0x24, 0x8C, 0xC8),
    "minecraft:yellow_concrete": (0xF0, 0xAC, 0x1C),
    "minecraft:lime_concrete": (0x58, 0xA8, 0x1C),
    "minecraft:pink_concrete": (0xD0, 0x60, 0x80),
    "minecraft:gray_concrete": (0x38, 0x3C, 0x40),
    "minecraft:light_gray_concrete": (0x7C, 0x7C, 0x74),
    "minecraft:cyan_concrete": (0x14, 0x74, 0x84),
    "minecraft:purple_concrete": (0x64, 0x28, 0xA0),
    "minecraft:blue_concrete": (0x2C, 0x30, 0x9C),
    "minecraft:brown_concrete": (0x5C, 0x38, 0x20),
    "minecraft:green_concrete": (0x48, 0x58, 0x24),
    "minecraft:red_concrete": (0x8C, 0x20, 0x1C),
    "minecraft:black_concrete": (0x08, 0x0C, 0x14),
    "minecraft:white_wool": (0xE8, 0xEC, 0xEC),
    "minecraft:orange_wool": (0xF0, 0x7C, 0x30),
    "minecraft:magenta_wool": (0xB8, 0x50, 0x9C),
    "minecraft:light_blue_wool": (0x60, 0xB0, 0xDC),
    "minecraft:yellow_wool": (0xF8, 0xCC, 0x30),
    "minecraft:lime_wool": (0x6C, 0xBC, 0x28),
    "minecraft:pink_wool": (0xE4, 0x8C, 0x9C),
    "minecraft:gray_wool": (0x40, 0x40, 0x44),
    "minecraft:light_gray_wool": (0x8C, 0x8C, 0x88),
    "minecraft:cyan_wool": (0x20, 0x84, 0x94),
    "minecraft:purple_wool": (0x78, 0x38, 0xBC),
    "minecraft:blue_wool": (0x34, 0x38, 0xAC),
    "minecraft:brown_wool": (0x6C, 0x44, 0x28),
    "minecraft:green_wool": (0x50, 0x66, 0x14),
    "minecraft:red_wool": (0x9C, 0x2C, 0x2C),
    "minecraft:black_wool": (0x14, 0x18, 0x20),
    "minecraft:oak_planks": (0xB8, 0x8C, 0x5C),
    "minecraft:spruce_planks": (0x80, 0x64, 0x3C),
    "minecraft:birch_planks": (0xCC, 0xBC, 0x94),
    "minecraft:jungle_planks": (0xB0, 0x80, 0x58),
    "minecraft:acacia_planks": (0xB8, 0x64, 0x38),
    "minecraft:dark_oak_planks": (0x50, 0x38, 0x24),
    "minecraft:crimson_planks": (0x6C, 0x30, 0x40),
    "minecraft:warped_planks": (0x30, 0x6C, 0x74),
}


def _hash_color(name):
    h = hash(name)
    r = (h & 0xFF0000) >> 16
    g = (h & 0x00FF00) >> 8
    b = h & 0x0000FF
    r = 60 + (r % 156)
    g = 60 + (g % 156)
    b = 60 + (b % 156)
    return (r, g, b)


def block_color(block_name):
    name = block_name
    if name.startswith("minecraft:"):
        pass
    else:
        name = "minecraft:" + name
    if name in BLOCK_COLORS:
        return BLOCK_COLORS[name]
    if "leaves" in name:
        return BLOCK_COLORS.get("minecraft:oak_leaves", (0x2C, 0x72, 0x0C))
    if "log" in name:
        return (0x7C, 0x60, 0x3C)
    if "planks" in name:
        return (0xB8, 0x8C, 0x5C)
    if "ore" in name:
        return (0x9A, 0x9A, 0x9A)
    if "terracotta" in name:
        return (0x9A, 0x62, 0x44)
    if "concrete" in name:
        return (0x80, 0x80, 0x80)
    if "wool" in name:
        return (0xC0, 0xC0, 0xC0)
    return _hash_color(name)


class RegionFile:
    SECTOR_SIZE = 4096

    def __init__(self, path):
        self._path = path
        self._fd = None

    def open(self):
        if self._fd is not None:
            return
        self._fd = open(self._path, "rb")
        self._offsets = []
        self._timestamps = []
        header = self._fd.read(8192)
        if len(header) < 8192:
            self._fd.close()
            self._fd = None
            raise IOError("Region file too short: {}".format(self._path))
        for i in range(1024):
            val = struct.unpack_from(">I", header, i * 4)[0]
            self._offsets.append(((val >> 8) & 0xFFFFFF, val & 0xFF))
        for i in range(1024):
            self._timestamps.append(struct.unpack_from(">I", header, 4096 + i * 4)[0])

    def close(self):
        if self._fd:
            self._fd.close()
            self._fd = None

    def chunk_exists(self, cx, cz):
        idx = (cx & 31) + (cz & 31) * 32
        return self._offsets[idx][0] != 0

    def read_chunk_data(self, cx, cz):
        idx = (cx & 31) + (cz & 31) * 32
        offset, sectors = self._offsets[idx]
        if offset == 0 or sectors == 0:
            return None
        self._fd.seek(offset * self.SECTOR_SIZE)
        hdr = self._fd.read(5)
        if len(hdr) < 5:
            return None
        length, compression = struct.unpack(">IB", hdr)
        data = self._fd.read(length)
        if len(data) < length:
            return None
        if compression == 1:
            return zlib.decompress(data, 15 + 32)
        elif compression == 2:
            return zlib.decompress(data)
        elif compression == 3:
            return data
        return data

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


def _decode_heightmap(heightmap_longs):
    heights = [0] * 256
    for i in range(256):
        long_idx = i // 7
        bit_offset = (i % 7) * 9
        if long_idx < len(heightmap_longs):
            heights[i] = (heightmap_longs[long_idx] >> bit_offset) & 0x1FF
    return heights


def _block_at_section(section_nbt, x, y, z):
    block_states = section_nbt.get("block_states", {})
    palette = block_states.get("palette", [])
    data = block_states.get("data", [])
    if not palette:
        return block_states.get("Name", "minecraft:air")
    if not data:
        return palette[0].get("Name", "minecraft:air") if palette else "minecraft:air"
    bits = max(4, math.ceil(math.log2(len(palette))))
    idx = (y * 16 + z) * 16 + x
    bit_pos = idx * bits
    long_idx = bit_pos // 64
    bit_off = bit_pos % 64
    if long_idx >= len(data):
        return palette[0].get("Name", "minecraft:air") if palette else "minecraft:air"
    mask = (1 << bits) - 1
    value = (data[long_idx] >> bit_off)
    if bit_off + bits > 64:
        if long_idx + 1 < len(data):
            value |= (data[long_idx + 1] << (64 - bit_off))
        value &= mask
    else:
        value &= mask
    if value < 0:
        value = value + (1 << bits)
    if value >= len(palette):
        return "minecraft:air"
    return palette[value].get("Name", "minecraft:air")


class MinimapEngine:

    def __init__(self, world_dir):
        self._world_dir = world_dir
        self._region_dir = os.path.join(world_dir, "region") if world_dir else None
        self._entities_dir = os.path.join(world_dir, "entities") if world_dir else None
        self._region_cache = {}
        self._chunk_cache = {}
        self._player_x = 0.0
        self._player_z = 0.0
        self._player_valid = False
        self._entities = []
        self._players = []
        self._disk_entities = []
        self._disk_scan_count = 0
        self._disk_scan_error = "pending"
        self._scan_attempts = 0
        self._scan_region_count = 0
        self._scan_doubles = 0
        self._scan_error = ""
        self._prev_centroids = []
        self._scan_cursor = 0
        self._focus_start = None
        self._active_start = 0

    @property
    def player_x(self):
        return self._player_x

    @property
    def player_z(self):
        return self._player_z

    @property
    def player_valid(self):
        return self._player_valid

    @property
    def entities(self):
        return self._entities

    @property
    def players(self):
        return self._players

    @property
    def disk_entities(self):
        return self._disk_entities

    @property
    def disk_scan_count(self):
        return self._disk_scan_count

    @property
    def disk_scan_error(self):
        return self._disk_scan_error

    @property
    def scan_attempts(self):
        return self._scan_attempts

    @property
    def scan_region_count(self):
        return self._scan_region_count

    @property
    def scan_doubles(self):
        return self._scan_doubles

    @property
    def scan_error(self):
        return self._scan_error

    def set_world_dir(self, world_dir):
        self._world_dir = world_dir
        self._region_dir = os.path.join(world_dir, "region") if world_dir else None
        self._entities_dir = os.path.join(world_dir, "entities") if world_dir else None
        self._region_cache.clear()
        self._chunk_cache.clear()

    def _get_region(self, rx, rz):
        if self._region_dir is None:
            return None
        key = (rx, rz)
        if key in self._region_cache:
            return self._region_cache[key]
        path = os.path.join(self._region_dir, "r.{}.{}.mca".format(rx, rz))
        if not os.path.exists(path):
            self._region_cache[key] = None
            return None
        rf = RegionFile(path)
        try:
            rf.open()
        except Exception:
            self._region_cache[key] = None
            return None
        self._region_cache[key] = rf
        return rf

    def _get_chunk(self, cx, cz):
        key = (cx, cz)
        if key in self._chunk_cache:
            return self._chunk_cache[key]
        rx = cx >> 5
        rz = cz >> 5
        rf = self._get_region(rx, rz)
        if rf is None:
            self._chunk_cache[key] = None
            return None
        try:
            raw = rf.read_chunk_data(cx, cz)
        except Exception:
            self._chunk_cache[key] = None
            return None
        if raw is None:
            self._chunk_cache[key] = None
            return None
        try:
            nbt = parse_nbt(raw)
        except Exception:
            self._chunk_cache[key] = None
            return None
        self._chunk_cache[key] = nbt
        return nbt

    def _read_chunk_entities(self, cx, cz):
        nbt = self._get_chunk(cx, cz)
        if nbt is None:
            return []
        raw_list = nbt.get("Entities", None)
        if raw_list is None:
            raw_list = nbt.get("entities", None)
        if raw_list is None:
            return []
        result = []
        for tag in raw_list:
            if not isinstance(tag, OrderedDict):
                continue
            eid = tag.get("id", "unknown")
            pos_tag = tag.get("Pos", None)
            if pos_tag is None or not isinstance(pos_tag, list) or len(pos_tag) < 3:
                continue
            pos = pos_tag
            rot_tag = tag.get("Rotation", None)
            yaw = 0.0
            if rot_tag is not None and isinstance(rot_tag, list) and len(rot_tag) >= 1:
                yaw = float(rot_tag[0])
            custom_name = ""
            cn = tag.get("CustomName", "")
            if cn and isinstance(cn, str):
                try:
                    import json
                    cnj = json.loads(cn)
                    if isinstance(cnj, dict) and "text" in cnj:
                        custom_name = cnj["text"]
                except Exception:
                    custom_name = cn
            result.append({
                "id": eid.replace("minecraft:", ""),
                "x": float(pos[0]),
                "y": float(pos[1]),
                "z": float(pos[2]),
                "yaw": yaw,
                "name": custom_name,
                "chunk": (cx, cz),
            })
        return result

    def _scan_disk_entities(self):
        self._disk_scan_count += 1
        self._disk_scan_error = ""
        if self._world_dir is None or not os.path.isdir(self._region_dir):
            self._disk_entities = []
            self._disk_scan_error = "no world/region dir"
            return
        try:
            px = int(self._player_x)
            pz = int(self._player_z)
            cx0 = (px >> 4) - 4
            cz0 = (pz >> 4) - 4
            ents = []
            chunks_read = 0
            used_entities_dir = False
            for dx in range(9):
                for dz in range(9):
                    ecx = cx0 + dx
                    ecz = cz0 + dz
                    chunk_ents = self._read_chunk_entities(ecx, ecz)
                    if self._get_chunk(ecx, ecz) is not None:
                        chunks_read += 1
                    for e in chunk_ents:
                        ents.append(e)
            if not ents and os.path.isdir(self._entities_dir):
                ents = self._read_entities_mca()
                if ents:
                    used_entities_dir = True
                    chunks_read = len(ents)
            self._disk_entities = ents
            if not ents and chunks_read == 0:
                self._disk_scan_error = "0 chunks readable in region"
            elif not ents:
                self._disk_scan_error = "{} chunks, 0 entities".format(chunks_read)
            else:
                self._disk_scan_error = ""
            if chunks_read > 0 and not ents:
                nbt = self._get_chunk(cx0 + 4, cz0 + 4)
                if nbt is not None:
                    keys = list(nbt.keys())[:8]
                    ekeys = [k for k in nbt.keys() if "entit" in k.lower()]
                    if ekeys:
                        self._disk_scan_error += " | ekeys:" + ",".join(ekeys)
                    else:
                        self._disk_scan_error += " | keys:" + ",".join(keys)
        except Exception as ex:
            self._disk_entities = []
            self._disk_scan_error = str(ex)[:80]

    def _read_entities_mca(self):
        ents = []
        px = int(self._player_x) >> 9
        pz = int(self._player_z) >> 9
        for drx in range(-1, 2):
            for drz in range(-1, 2):
                rx = px + drx
                rz = pz + drz
                path = os.path.join(self._entities_dir, "r.{}.{}.nbt.mca".format(rx, rz))
                if not os.path.exists(path):
                    continue
                ents.extend(self._read_entities_from_entities_mca(path))
                if len(ents) > 200:
                    return ents
        return ents

    def _read_entities_from_entities_mca(self, path):
        ents = []
        try:
            with open(path, "rb") as f:
                header = f.read(8192)
                if len(header) < 8192:
                    return []
                offsets = []
                for i in range(1024):
                    val = struct.unpack_from(">I", header, i * 4)[0]
                    offsets.append(((val >> 8) & 0xFFFFFF, val & 0xFF))
                for idx, (offset, sectors) in enumerate(offsets):
                    if offset == 0 or sectors == 0:
                        continue
                    try:
                        f.seek(offset * 4096)
                        hdr = f.read(5)
                        if len(hdr) < 5:
                            continue
                        length, compression = struct.unpack(">IB", hdr)
                        data = f.read(length)
                        if len(data) < length:
                            continue
                        if compression == 1:
                            raw = zlib.decompress(data, 15 + 32)
                        elif compression == 2:
                            raw = zlib.decompress(data)
                        elif compression == 3:
                            raw = data
                        else:
                            continue
                        tag = parse_nbt(raw)
                        if not isinstance(tag, OrderedDict):
                            continue
                        eid = tag.get("id", "unknown").replace("minecraft:", "")
                        pos_tag = tag.get("Pos", None)
                        if pos_tag is None or not isinstance(pos_tag, list) or len(pos_tag) < 3:
                            continue
                        rot_tag = tag.get("Rotation", None)
                        yaw = 0.0
                        if rot_tag and isinstance(rot_tag, list) and len(rot_tag) >= 1:
                            yaw = float(rot_tag[0])
                        ents.append({
                            "id": eid,
                            "x": float(pos_tag[0]),
                            "y": float(pos_tag[1]),
                            "z": float(pos_tag[2]),
                            "yaw": yaw,
                            "name": "",
                            "chunk": (int(float(pos_tag[0])) >> 4, int(float(pos_tag[2])) >> 4),
                        })
                    except Exception:
                        continue
        except Exception:
            pass
        return ents

    def _get_top_block(self, cx, cz, bx, bz):
        nbt = self._get_chunk(cx, cz)
        if nbt is None:
            return "minecraft:air"
        sections_list = nbt.get("sections", [])
        min_y = nbt.get("yPos", 0)
        heightmaps = nbt.get("Heightmaps", {})
        motion_blocking = heightmaps.get("MOTION_BLOCKING", None)
        if motion_blocking is not None and isinstance(motion_blocking, list):
            heights = _decode_heightmap(motion_blocking)
            height = heights[bx + bz * 16]
            if height <= 0:
                return "minecraft:air"
            section_idx = (height >> 4) - min_y
            if 0 <= section_idx < len(sections_list):
                section = sections_list[section_idx]
                sy = height & 15
                return _block_at_section(section, bx, sy, bz)
        return self._get_top_block_fallback(nbt, bx, bz)

    def _get_top_block_fallback(self, nbt, bx, bz):
        sections_list = nbt.get("sections", [])
        if not sections_list:
            return "minecraft:air"
        min_y = nbt.get("yPos", 0)
        for section in reversed(sections_list):
            sec_y = section.get("Y", 0)
            for y in range(15, -1, -1):
                block = _block_at_section(section, bx, y, bz)
                if block != "minecraft:air" and block != "minecraft:cave_air" and block != "minecraft:void_air":
                    return block
        return "minecraft:air"

    def _col_blocks(self, cx, cz, bx, bz, top_down=True):
        """自上而下(或自下而上)扫描一列方块，逐块产出 block 名字。"""
        nbt = self._get_chunk(cx, cz)
        if nbt is None:
            return
        sections_list = nbt.get("sections", [])
        order = reversed(sections_list) if top_down else sections_list
        for section in order:
            for y in (range(15, -1, -1) if top_down else range(16)):
                yield _block_at_section(section, bx, y, bz)

    def get_cave_tile(self, block_x, block_z):
        """空洞(地下)视图：地表实心，但向下挖出空气/空洞/虚空即标记为空洞。"""
        cx = block_x >> 4
        cz = block_z >> 4
        bx = block_x & 15
        bz = block_z & 15
        if bx < 0:
            bx += 16
        if bz < 0:
            bz += 16
        found_solid = False
        seen = 0
        for name in self._col_blocks(cx, cz, bx, bz, top_down=True):
            seen += 1
            if name in ("minecraft:air", "minecraft:cave_air", "minecraft:void_air"):
                if found_solid:
                    return name, (16, 8, 8)      # 地表下有空洞 → 暗红
            else:
                found_solid = True
            if seen > 96:                        # 只查地表往下 ~96 层，控制开销
                break
        if found_solid:
            return "minecraft:stone", (0x4A, 0x4A, 0x52)  # 实心岩石
        return "minecraft:air", (12, 12, 16)             # 虚空/未加载

    def get_ore_tile(self, block_x, block_z):
        """矿石视图：自下而上整列，把首个所属矿石显色；无矿则回落地形。"""
        cx = block_x >> 4
        cz = block_z >> 4
        bx = block_x & 15
        bz = block_z & 15
        if bx < 0:
            bx += 16
        if bz < 0:
            bz += 16
        seen = 0
        for name in self._col_blocks(cx, cz, bx, bz, top_down=True):
            seen += 1
            if "ore" in name:
                return name, block_color(name)
            if seen > 128:
                break
        # 无矿：回落“地表方块颜色”作为底层(看不出矿但在同一比例下不刺眼)
        return self.get_tile(block_x, block_z)

    def get_tile(self, block_x, block_z):
        cx = block_x >> 4
        cz = block_z >> 4
        bx = block_x & 15
        bz = block_z & 15
        if bx < 0:
            bx += 16
        if bz < 0:
            bz += 16
        name = self._get_top_block(cx, cz, bx, bz)
        return name, block_color(name)

    def get_tile_mode(self, mode, block_x, block_z):
        if mode == "cave":
            return self.get_cave_tile(block_x, block_z)
        if mode == "ore":
            return self.get_ore_tile(block_x, block_z)
        return self.get_tile(block_x, block_z)

    def scan_player_position(self, pid):
        from .stealth_memory import _syscall_read_vm, query_memory_regions
        import threading

        self._scan_attempts += 1

        h_proc = ctypes.windll.kernel32.OpenProcess(PROCESS_VM_READ, False, pid)
        if not h_proc:
            self._player_valid = False
            self._scan_error = "OpenProcess failed err=0x{:08X}".format(
                ctypes.get_last_error())
            return
        try:
            regions = query_memory_regions(pid)
        except Exception as _e:
            ctypes.windll.kernel32.CloseHandle(h_proc)
            self._player_valid = False
            self._scan_error = "query_memory_regions: {}".format(_e)
            return

        heap_regions = _scan_candidate_regions(regions)
        if not heap_regions:
            ctypes.windll.kernel32.CloseHandle(h_proc)
            self._player_valid = False
            self._scan_error = "no candidate regions ({} total)".format(len(regions))
            return

        self._scan_region_count = len(heap_regions)
        self._scan_error = ""

        def _scan_regions(work_items, results, lock):
            local_votes = {}
            total_d = 0
            for base, size in work_items:
                addr = base
                remaining = size
                chunk_sz = 16 * 1024 * 1024
                while remaining > 24:
                    read_sz = min(chunk_sz, remaining)
                    try:
                        buf = _syscall_read_vm(h_proc, addr, read_sz)
                    except Exception:
                        addr += min(chunk_sz, remaining)
                        remaining -= chunk_sz
                        continue
                    blen = len(buf)
                    if blen < 24:
                        addr += min(chunk_sz, remaining)
                        remaining -= chunk_sz
                        continue
                    aligned = (blen // 8) * 8
                    try:
                        doubles = memoryview(buf[:aligned]).cast("d")
                    except Exception:
                        addr += min(chunk_sz, remaining)
                        remaining -= chunk_sz
                        continue
                    nd = aligned // 8
                    total_d += nd
                    if _HAS_NUMPY:
                        # 向量化表决：numpy 跑在 C 层、自动释放 GIL，扫描线程可并行，
                        # 速度比逐 double 的 Python 循环快数十倍，显著降低卡顿。
                        try:
                            arr = np.frombuffer(buf, dtype=np.float64, count=nd)
                            x = arr[:-2]
                            y = arr[1:-1]
                            z = arr[2:]
                            valid = (
                                (np.abs(x) <= 30000000.0) &
                                (y >= -64.0) & (y < 384.0) &
                                (np.abs(z) <= 30000000.0) &
                                np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
                            )
                            if valid.any():
                                xv = x[valid]
                                yv = y[valid]
                                zv = z[valid]
                                # 与纯 Python 路径保持一致：int() 截断向零 + >>4 向下取整
                                cx = np.trunc(xv).astype(np.int64) >> np.int64(4)
                                cz = np.trunc(zv).astype(np.int64) >> np.int64(4)
                                keys = (cx << np.int64(32)) | (cz & np.int64(0xFFFFFFFF))
                                uniq, cnt = np.unique(keys, return_counts=True)
                                inv = np.searchsorted(uniq, keys)
                                sx = np.bincount(inv, weights=xv, minlength=uniq.size)
                                sy = np.bincount(inv, weights=yv, minlength=uniq.size)
                                sz = np.bincount(inv, weights=zv, minlength=uniq.size)
                                sx2 = np.bincount(inv, weights=xv * xv, minlength=uniq.size)
                                sz2 = np.bincount(inv, weights=zv * zv, minlength=uniq.size)
                                for k, c, ssx, ssy, ssz, ssx2, ssz2 in zip(
                                        uniq, cnt, sx, sy, sz, sx2, sz2):
                                    cxi = int(k >> np.int64(32))
                                    czi = int(k & np.int64(0xFFFFFFFF))
                                    if czi >= 0x80000000:
                                        czi -= 0x100000000
                                    key = (cxi, czi)
                                    rec = local_votes.get(key)
                                    if rec is None:
                                        local_votes[key] = {
                                            "count": int(c), "sum_x": float(ssx),
                                            "sum_y": float(ssy), "sum_z": float(ssz),
                                            "sum_x2": float(ssx2), "sum_z2": float(ssz2),
                                        }
                                    else:
                                        rec["count"] += int(c)
                                        rec["sum_x"] += float(ssx)
                                        rec["sum_y"] += float(ssy)
                                        rec["sum_z"] += float(ssz)
                                        rec["sum_x2"] += float(ssx2)
                                        rec["sum_z2"] += float(ssz2)
                        except Exception:
                            pass
                    else:
                        for i in range(nd - 2):
                            x = doubles[i]
                            if x != x or x > 30000000.0 or x < -30000000.0:
                                continue
                            y = doubles[i + 1]
                            if y != y or y < -64.0 or y > 384.0:
                                continue
                            z = doubles[i + 2]
                            if z != z or z > 30000000.0 or z < -30000000.0:
                                continue
                            cx = int(x) >> 4
                            cz = int(z) >> 4
                            key = (cx, cz)
                            if key not in local_votes:
                                local_votes[key] = {
                                    "count": 0, "sum_x": 0.0, "sum_y": 0.0,
                                    "sum_z": 0.0, "sum_x2": 0.0, "sum_z2": 0.0,
                                }
                            local_votes[key]["count"] += 1
                            local_votes[key]["sum_x"] += x
                            local_votes[key]["sum_y"] += y
                            local_votes[key]["sum_z"] += z
                            local_votes[key]["sum_x2"] += x * x
                            local_votes[key]["sum_z2"] += z * z
                    addr += chunk_sz
                    remaining -= chunk_sz
            with lock:
                results.append((local_votes, total_d))

        # 轮转扫描窗口：堆前的静态元数据不包含玩家/实体，因此不能永远只扫开头。
        # 每周期只扫一个 WINDOW 窗口(CPU 恒定、低卡顿)，窗口在整段堆跨度内推进：
        # 一旦在某窗口发现“移动簇”(玩家/实体)，就锁定该窗口持续跟踪；否则继续向前扫。
        WINDOW = 32 * 1024 * 1024
        spans = sorted(((r["base"], r["size"]) for r in heap_regions),
                       key=lambda s: s[1], reverse=True)
        work = []
        if spans:
            rot_base, rot_span = spans[0]
            if rot_span > WINDOW:
                if self._focus_start is not None:
                    start = self._focus_start
                else:
                    start = self._scan_cursor % (rot_span - WINDOW + 1)
            else:
                start = 0
            work.append((rot_base + start, min(WINDOW, rot_span)))
            self._active_start = start
            # 其余较小区域：扫其开头一小段，附带补充不同提交区。
            for base, sp in spans[1:]:
                work.append((base, min(sp, WINDOW)))

        num_workers = min(4, max(1, len(work)))
        per_worker = (len(work) + num_workers - 1) // num_workers
        threads = []
        results = []
        lock = threading.Lock()
        for i in range(num_workers):
            chunk = work[i * per_worker:(i + 1) * per_worker]
            if not chunk:
                break
            t = threading.Thread(target=_scan_regions, args=(chunk, results, lock), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        ctypes.windll.kernel32.CloseHandle(h_proc)

        chunk_votes = {}
        total_doubles = 0
        for local_votes, td in results:
            total_doubles += td
            for key, val in local_votes.items():
                if key not in chunk_votes:
                    chunk_votes[key] = val.copy()
                else:
                    chunk_votes[key]["count"] += val["count"]
                    chunk_votes[key]["sum_x"] += val["sum_x"]
                    chunk_votes[key]["sum_y"] += val["sum_y"]
                    chunk_votes[key]["sum_z"] += val["sum_z"]
                    chunk_votes[key]["sum_x2"] += val["sum_x2"]
                    chunk_votes[key]["sum_z2"] += val["sum_z2"]

        self._scan_doubles = total_doubles
        if not chunk_votes:
            # 无有效簇：若之前已定位则保留上次位置，否则清空。同时让轮转游标
            # 前移(该窗口没有实体数据)，避免在一个空窗口上原地踏步。
            self._focus_start = None
            self._scan_cursor += WINDOW
            if not self._player_valid:
                self._players = []
                self._entities = []
            return

        # 一个真实实体在堆中同一 tick 的多份坐标副本几乎完全相等 → 方差极小；
        # 杂散噪音/静态元数据(double 落在合法范围但坐标簇非常发散)会被紧凑度过滤掉。
        COMPACT_DIST = 4.0          # 簇半径上限（方块）
        COMPACT_VAR = COMPACT_DIST * COMPACT_DIST
        candidates = []
        for key, val in chunk_votes.items():
            count = val["count"]
            if count < 3:
                continue
            avg_x = val["sum_x"] / count
            avg_y = val["sum_y"] / count
            avg_z = val["sum_z"] / count
            var_x = max(0.0, val["sum_x2"] / count - avg_x * avg_x)
            var_z = max(0.0, val["sum_z2"] / count - avg_z * avg_z)
            if (var_x + var_z) > COMPACT_VAR:
                continue
            candidates.append({
                "x": avg_x, "y": avg_y, "z": avg_z, "chunk": key, "count": count,
                "moved": 0.0, "spread": math.sqrt(var_x + var_z),
            })

        if not candidates:
            if not self._player_valid:
                self._players = []
                self._entities = []
            self._prev_centroids = []
            return

        prev = self._prev_centroids
        for c in candidates:
            if prev:
                c["moved"] = min(
                    math.hypot(c["x"] - px, c["z"] - pz) for px, pz in prev)
            else:
                c["moved"] = 0.0
        self._prev_centroids = [(c["x"], c["z"]) for c in candidates]

        # 关键：用“本周期相对上一周期是否发生位移”区分活实体(玩家/怪物)与静态
        # 元数据。堆前的 (0,0) 静态簇 vote 再高也不移动，因此不会被当作玩家。
        live = [c for c in candidates if c["moved"] >= 1.0]
        chosen = None
        if live:
            chosen = max(live, key=lambda c: c["count"])
            self._player_x = chosen["x"]
            self._player_z = chosen["z"]
            self._player_valid = True
        # 实体：显示本窗口内所有紧凑簇(除玩家簇)，真实实体会随之移动并刷新，
        # 静态落后簇保持不动但不至于让实体列表变空(避免上一轮“扫不到实体”)。
        self._players = []
        self._entities = []
        for c in candidates:
            if chosen is not None and c is chosen:
                continue
            entry = {
                "x": c["x"], "y": c["y"], "z": c["z"], "chunk": c["chunk"],
                "count": c["count"], "spread": c["spread"],
            }
            if c["count"] >= 8:
                self._players.append(entry)
            elif c["count"] >= 3:
                self._entities.append(entry)

        # 跟踪窗口调度：发现移动簇 → 锁定当前窗口持续跟住玩家/实体；
        # 否则 → 清空锁定并让轮转游标前移，去扫描堆里更深处尚未覆盖的区域。
        if live:
            self._focus_start = self._active_start
        else:
            self._focus_start = None
            self._scan_cursor += WINDOW