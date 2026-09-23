#!/bin/sh
# 使い捨ての btrfs を、ループバックデバイスの上に作る / 片付ける。
#
# デバイスの追加・取り外し・置換は、試した時点で本物のアレイを変えてしまう。
# それらを安全に確かめるための「実験室」を用意するのがこのスクリプトの役目。
#
# **実機のディスクには一切触れない。** 触るのは以下だけ:
#   - $LAB 配下に作るイメージファイル (既定: /var/tmp/btrfs-timeline-lab)
#   - そのイメージに割り当てた loop デバイス
#   - $LAB/mnt へのマウント
#
# 安全のため、以下を守る:
#   - mkfs をかける前に、対象が /dev/loop で始まることを 1 台ずつ確かめる
#   - 片付けでは、このスクリプトが記録した loop デバイスしか切り離さない
#   - マウント先が想定のラベルであることを確かめてから umount する
#
# losetup / mkfs.btrfs / mount には root が要るので、sudo を付けて実行する。

set -eu

LAB=${BTRFS_TIMELINE_LAB:-/var/tmp/btrfs-timeline-lab}
IMAGES=$LAB/images
MOUNT=$LAB/mnt
LOOPS=$LAB/loops
LABEL=btrfs-timeline-lab

COUNT=${COUNT:-4}
SIZE=${SIZE:-512M}
PROFILE=${PROFILE:-raid1}

# マウント先を使う人。sudo 経由なら元の利用者に渡す
OWNER=${SUDO_USER:-$(id -un)}

die() {
    echo "error: $*" >&2
    exit 1
}

require_root() {
    [ "$(id -u)" = 0 ] || die "root で実行してください (sudo $0 $ACTION)"
}

# 実験室の場所が明らかに妙なら止める。うっかり実データを消さないための保険
check_lab_path() {
    case $LAB in
        /var/tmp/*|/tmp/*) ;;
        *) die "LAB は /var/tmp か /tmp の下にしてください (今: $LAB)" ;;
    esac
}

setup() {
    require_root
    check_lab_path
    [ -e "$LOOPS" ] && die "既に作られています。先に $0 teardown を実行してください"

    mkdir -p "$IMAGES" "$MOUNT"
    : > "$LOOPS"

    echo "イメージを $COUNT 個 ($SIZE) 作ります: $IMAGES"
    i=1
    while [ "$i" -le "$COUNT" ]; do
        image=$IMAGES/disk$i.img
        truncate -s "$SIZE" "$image"
        loop=$(losetup --find --show "$image")
        # 割り当てたものだけを記録する。片付けではこれしか触らない
        echo "$loop" >> "$LOOPS"
        echo "  $image -> $loop"
        i=$((i + 1))
    done

    devices=$(tr '\n' ' ' < "$LOOPS")

    # mkfs の直前に、1 台ずつ loop デバイスであることを確かめる
    for device in $devices; do
        case $device in
            /dev/loop[0-9]*) ;;
            *) die "loop デバイスではありません: $device (中止します)" ;;
        esac
    done

    echo "btrfs を作ります: profile=$PROFILE"
    mkfs.btrfs -q -L "$LABEL" -d "$PROFILE" -m "$PROFILE" $devices

    first=$(head -1 "$LOOPS")
    mount "$first" "$MOUNT"
    chown "$OWNER" "$MOUNT"

    # 履歴まわりも試せるよう、スナップショットの置き場と中身を用意する
    su - "$OWNER" -c "btrfs subvolume create '$MOUNT/data'" >/dev/null
    su - "$OWNER" -c "mkdir -p '$MOUNT/.snapshots'"
    su - "$OWNER" -c "echo 'first version' > '$MOUNT/data/notes.txt'"

    echo
    echo "用意できました:"
    echo "  マウント先 : $MOUNT"
    echo "  デバイス   : $devices"
    echo
    echo "片付けるとき: sudo $0 teardown"
}

teardown() {
    require_root
    check_lab_path

    if mountpoint -q "$MOUNT" 2>/dev/null; then
        # 想定したラベルのものであることを確かめてから外す
        found=$(findmnt -n -o SOURCE --target "$MOUNT" 2>/dev/null || true)
        case $found in
            /dev/loop[0-9]*) umount "$MOUNT" && echo "umount: $MOUNT" ;;
            *) die "$MOUNT に loop 以外のものがマウントされています: $found" ;;
        esac
    fi

    if [ -f "$LOOPS" ]; then
        while read -r loop; do
            [ -n "$loop" ] || continue
            case $loop in
                /dev/loop[0-9]*) losetup -d "$loop" 2>/dev/null && echo "切り離し: $loop" ;;
                *) echo "飛ばします (loop ではありません): $loop" >&2 ;;
            esac
        done < "$LOOPS"
    fi

    rm -rf "$IMAGES" "$LOOPS"
    rmdir "$MOUNT" 2>/dev/null || true
    rmdir "$LAB" 2>/dev/null || true
    echo "片付けました: $LAB"
}

status() {
    if [ ! -f "$LOOPS" ]; then
        echo "実験室はありません ($LAB)"
        return 0
    fi
    echo "実験室: $LAB"
    echo "デバイス:"
    sed 's/^/  /' "$LOOPS"
    if mountpoint -q "$MOUNT" 2>/dev/null; then
        echo "マウント中: $MOUNT"
    else
        echo "マウントされていません: $MOUNT"
    fi
}

ACTION=${1:-}
case $ACTION in
    setup) setup ;;
    teardown) teardown ;;
    status) status ;;
    *)
        echo "使い方: $0 {setup|teardown|status}"
        echo
        echo "  setup     ループバック上に使い捨ての btrfs を作る (root が必要)"
        echo "  teardown  作ったものを全て片付ける (root が必要)"
        echo "  status    今の状態を表示する"
        echo
        echo "環境変数: COUNT=$COUNT SIZE=$SIZE PROFILE=$PROFILE"
        echo "          BTRFS_TIMELINE_LAB=$LAB"
        exit 1
        ;;
esac
