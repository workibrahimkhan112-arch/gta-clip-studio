import { forwardRef } from 'react';
import { HugeiconsIcon, type HugeiconsIconProps, type IconSvgElement } from '@hugeicons/react';
import {
  Activity01Icon,
  AlertCircleIcon,
  Alert02Icon,
  ArrowLeft01Icon,
  ArrowRight01Icon,
  ArrowUpRight01Icon,
  CaptionsIcon,
  CheckIcon,
  CheckmarkCircle01Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  ChevronUpIcon,
  CircleIcon,
  ClapperboardIcon,
  Clock03Icon,
  CpuIcon,
  Database01Icon,
  Download01Icon,
  FileVideoIcon,
  Film01Icon,
  FolderClosedIcon,
  FolderOpenIcon,
  FolderPlusIcon,
  GaugeIcon,
  HardDriveIcon,
  HistoryIcon,
  InformationCircleIcon,
  LayoutDashboardIcon,
  Link02Icon,
  ListChecksIcon,
  LoaderCircleIcon,
  Menu01Icon,
  MinusIcon,
  MonitorPlayIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RadioIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SaveIcon,
  ScanLineIcon,
  Search01Icon,
  Settings02Icon,
  ShieldCheckIcon,
  SlidersHorizontalIcon,
  SparklesIcon,
  Square01Icon,
  Timer01Icon,
  Delete02Icon,
  Upload01Icon,
  VolumeHighIcon,
  VolumeXIcon,
  WavesIcon,
  WifiOff01Icon,
  XIcon,
  CancelCircleIcon,
  YoutubeIcon,
} from '@hugeicons/core-free-icons';

type IconProps = Omit<HugeiconsIconProps, 'icon'>;
function wrap(asset: IconSvgElement) {
  return forwardRef<SVGSVGElement, IconProps>((props, ref) => (
    <HugeiconsIcon icon={asset} strokeWidth={1.7} {...props} ref={ref} />
  ));
}
export type AppIcon = ReturnType<typeof wrap>;
export const Activity = wrap(Activity01Icon);
export const AlertCircle = wrap(AlertCircleIcon);
export const AlertTriangle = wrap(Alert02Icon);
export const ArrowLeft = wrap(ArrowLeft01Icon);
export const ArrowRight = wrap(ArrowRight01Icon);
export const ArrowUpRight = wrap(ArrowUpRight01Icon);
export const Captions = wrap(CaptionsIcon);
export const Check = wrap(CheckIcon);
export const CheckCircle2 = wrap(CheckmarkCircle01Icon);
export const ChevronDown = wrap(ChevronDownIcon);
export const ChevronRight = wrap(ChevronRightIcon);
export const ChevronUp = wrap(ChevronUpIcon);
export const Circle = wrap(CircleIcon);
export const Clapperboard = wrap(ClapperboardIcon);
export const Clock3 = wrap(Clock03Icon);
export const Cpu = wrap(CpuIcon);
export const Database = wrap(Database01Icon);
export const Download = wrap(Download01Icon);
export const FileVideo = wrap(FileVideoIcon);
export const Film = wrap(Film01Icon);
export const FolderClosed = wrap(FolderClosedIcon);
export const FolderOpen = wrap(FolderOpenIcon);
export const FolderPlus = wrap(FolderPlusIcon);
export const Gauge = wrap(GaugeIcon);
export const HardDrive = wrap(HardDriveIcon);
export const History = wrap(HistoryIcon);
export const Info = wrap(InformationCircleIcon);
export const LayoutDashboard = wrap(LayoutDashboardIcon);
export const Link2 = wrap(Link02Icon);
export const ListChecks = wrap(ListChecksIcon);
export const Loader2 = wrap(LoaderCircleIcon);
export const Menu = wrap(Menu01Icon);
export const Minus = wrap(MinusIcon);
export const MonitorPlay = wrap(MonitorPlayIcon);
export const PanelLeftClose = wrap(PanelLeftCloseIcon);
export const PanelLeftOpen = wrap(PanelLeftOpenIcon);
export const Pause = wrap(PauseIcon);
export const Play = wrap(PlayIcon);
export const Plus = wrap(PlusIcon);
export const Radio = wrap(RadioIcon);
export const RefreshCw = wrap(RefreshCwIcon);
export const RotateCcw = wrap(RotateCcwIcon);
export const Save = wrap(SaveIcon);
export const ScanLine = wrap(ScanLineIcon);
export const Search = wrap(Search01Icon);
export const Settings2 = wrap(Settings02Icon);
export const ShieldCheck = wrap(ShieldCheckIcon);
export const SlidersHorizontal = wrap(SlidersHorizontalIcon);
export const Sparkles = wrap(SparklesIcon);
export const Square = wrap(Square01Icon);
export const Timer = wrap(Timer01Icon);
export const Trash2 = wrap(Delete02Icon);
export const Upload = wrap(Upload01Icon);
export const Volume2 = wrap(VolumeHighIcon);
export const VolumeX = wrap(VolumeXIcon);
export const Waves = wrap(WavesIcon);
export const WifiOff = wrap(WifiOff01Icon);
export const X = wrap(XIcon);
export const XCircle = wrap(CancelCircleIcon);
export const Youtube = wrap(YoutubeIcon);
