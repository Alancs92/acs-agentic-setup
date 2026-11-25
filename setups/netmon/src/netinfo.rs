//! Interface counters, gateway MAC and boot time, via small bounded helper
//! commands (`netstat -ibn`, `route`, `arp`, `sysctl`).
//!
//! Builder's call: parse `netstat -ibn` rather than call `getifaddrs` through
//! hand-written FFI. It keeps the crate 100% safe Rust and its output is
//! streamed line by line; the helper's cost is outside netmon's own RSS/CPU.

use std::net::IpAddr;
use std::process::Command;
use std::time::Duration;

use crate::delta::{IfCounters, IfKind};
use crate::nettop::{stream_lines, RunError};

const HELPER_TIMEOUT: Duration = Duration::from_secs(3);

fn helper(env: &str, default: &str) -> Command {
    Command::new(std::env::var_os(env).unwrap_or_else(|| default.into()))
}

/// `en<digits>` -> Physical, `utun<digits>` -> Tunnel, everything else ignored.
fn classify(name: &str) -> Option<IfKind> {
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    if let Some(r) = name.strip_prefix("utun") {
        digits(r).then_some(IfKind::Tunnel)
    } else if let Some(r) = name.strip_prefix("en") {
        digits(r).then_some(IfKind::Physical)
    } else {
        None
    }
}

/// One `netstat -ibn` line -> counters, only for `<Link#n>` rows of `en*` and
/// `utun*`. Columns: Name Mtu Network [Address] Ipkts Ierrs Ibytes Opkts Oerrs
/// Obytes Coll. The Address column is empty for tunnels, so index from the end.
pub fn parse_netstat_line(line: &str) -> Option<IfCounters> {
    let t: Vec<&str> = line.split_whitespace().collect();
    if t.len() < 10 || !t[2].starts_with("<Link") {
        return None;
    }
    let name = t[0].trim_end_matches('*');
    let kind = classify(name)?;
    Some(IfCounters {
        name: name.to_string(),
        kind,
        bytes_in: t[t.len() - 5].parse().ok()?,
        bytes_out: t[t.len() - 2].parse().ok()?,
    })
}

pub fn read_interfaces() -> Result<Vec<IfCounters>, RunError> {
    let mut out = Vec::new();
    let mut cmd = helper("NETMON_NETSTAT", "/usr/sbin/netstat");
    cmd.arg("-ibn");
    let status = stream_lines(&mut cmd, HELPER_TIMEOUT, |l| {
        if let Some(c) = parse_netstat_line(l) {
            out.push(c);
        }
    })?;
    if status.success() {
        Ok(out)
    } else {
        Err(RunError::Exit(status.code()))
    }
}

/// Physical interfaces that have carried traffic, `en0` first (numeric order):
/// the candidates whose gateway we try to resolve.
pub fn gateway_candidates(ifs: &[IfCounters]) -> Vec<&str> {
    let mut v: Vec<(u32, &str)> = ifs
        .iter()
        .filter(|c| c.kind == IfKind::Physical && (c.bytes_in > 0 || c.bytes_out > 0))
        .map(|c| (c.name[2..].parse().unwrap_or(u32::MAX), c.name.as_str()))
        .collect();
    v.sort();
    v.into_iter().map(|(_, n)| n).collect()
}

/// `route -n get -ifscope enX default` -> gateway IP, if there is one.
pub fn parse_route_gateway(line: &str) -> Option<IpAddr> {
    let rest = line.trim().strip_prefix("gateway:")?;
    rest.trim().parse().ok()
}

/// `arp -n <ip>` -> normalised MAC. `(incomplete)` and no-entry give `None`.
pub fn parse_arp_mac(line: &str) -> Option<String> {
    let mut it = line.split_whitespace();
    while let Some(tok) = it.next() {
        if tok == "at" {
            return normalize_mac(it.next()?);
        }
    }
    None
}

/// Lower-case, zero-padded `aa:bb:cc:dd:ee:ff` (macOS arp prints `a:b:c:...`).
pub fn normalize_mac(s: &str) -> Option<String> {
    let parts: Vec<&str> = s.split(':').collect();
    if parts.len() != 6 {
        return None;
    }
    let mut out = String::with_capacity(17);
    for (i, p) in parts.iter().enumerate() {
        if p.is_empty() || p.len() > 2 || !p.bytes().all(|b| b.is_ascii_hexdigit()) {
            return None;
        }
        if i > 0 {
            out.push(':');
        }
        if p.len() == 1 {
            out.push('0');
        }
        out.push_str(&p.to_ascii_lowercase());
    }
    Some(out)
}

/// `sysctl -n kern.boottime` -> boot epoch seconds.
pub fn parse_boottime(s: &str) -> Option<i64> {
    let rest = s.split("sec =").nth(1)?;
    let n: String = rest
        .trim_start()
        .chars()
        .take_while(|c| c.is_ascii_digit())
        .collect();
    n.parse().ok()
}

pub fn boot_time() -> Option<i64> {
    let mut cmd = helper("NETMON_SYSCTL", "/usr/sbin/sysctl");
    cmd.args(["-n", "kern.boottime"]);
    let mut bt = None;
    let st = stream_lines(&mut cmd, HELPER_TIMEOUT, |l| {
        if bt.is_none() {
            bt = parse_boottime(l);
        }
    });
    st.ok().and(bt)
}

fn gateway_ip(ifname: &str) -> Option<IpAddr> {
    let mut cmd = helper("NETMON_ROUTE", "/sbin/route");
    cmd.args(["-n", "get", "-ifscope", ifname, "default"]);
    let mut gw = None;
    stream_lines(&mut cmd, HELPER_TIMEOUT, |l| {
        if gw.is_none() {
            gw = parse_route_gateway(l);
        }
    })
    .ok()?;
    gw
}

fn arp_mac(ip: IpAddr) -> Option<String> {
    let mut cmd = helper("NETMON_ARP", "/usr/sbin/arp");
    cmd.arg("-n").arg(ip.to_string());
    let mut mac = None;
    stream_lines(&mut cmd, HELPER_TIMEOUT, |l| {
        if mac.is_none() {
            mac = parse_arp_mac(l);
        }
    })
    .ok()?;
    mac
}

/// Gateway MAC of the first physical interface that has one. Never reads the
/// plain default route: under VPN that is a `utun*` with no gateway MAC.
pub fn gateway_mac(ifs: &[IfCounters]) -> Option<String> {
    gateway_candidates(ifs)
        .into_iter()
        .find_map(|n| gateway_ip(n).and_then(arp_mac))
}

#[cfg(test)]
mod tests {
    use super::*;

    const NETSTAT: &str = "\
Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
lo0        16384 <Link#1>                      232199211     0 369674357607 232199211     0 369674357607     0
lo0        16384 127           127.0.0.1       232199211     - 369674357607 232199211     - 369674357607     -
gif0*      1280  <Link#2>                             0     0          0        0     0          0     0
en4        1500  <Link#7>    4e:67:56:50:53:79        0     0          0        0     0          0     0
en0        1500  <Link#14>   b6:ab:a5:21:44:40 105109409     0 58364980385 98976733     0 66782292735     0
en0        1500  192.168.68    192.168.68.101  105109409     - 58364980385 98976733     - 66782292735     -
en5        1500  <Link#20>   aa:bb:cc:dd:ee:ff   5000     0     123456     4000     0     654321     0
bridge0    1500  <Link#15>   36:aa:61:8b:be:80        0     0          0        6     0        540     0
utun4      1280  <Link#22>                       478019     0  249921546   427856     0   44215074     0
utun4      1280  100.116.123.8 100.116.123.86    478019     -  249921546   427856     -   44215074     -
utunx      1280  <Link#23>                            1     0          1        1     0          1     0
";

    fn all() -> Vec<IfCounters> {
        NETSTAT.lines().filter_map(parse_netstat_line).collect()
    }

    #[test]
    fn netstat_keeps_only_link_rows_of_en_and_utun() {
        let names: Vec<_> = all().iter().map(|c| c.name.clone()).collect();
        assert_eq!(names, ["en4", "en0", "en5", "utun4"]);
    }

    #[test]
    fn netstat_counters_with_and_without_address_column() {
        let v = all();
        let en0 = v.iter().find(|c| c.name == "en0").unwrap();
        assert_eq!((en0.bytes_in, en0.bytes_out), (58364980385, 66782292735));
        assert_eq!(en0.kind, IfKind::Physical);
        let tun = v.iter().find(|c| c.name == "utun4").unwrap();
        assert_eq!((tun.bytes_in, tun.bytes_out), (249921546, 44215074));
        assert_eq!(tun.kind, IfKind::Tunnel);
    }

    #[test]
    fn netstat_garbage_is_none() {
        assert!(parse_netstat_line("").is_none());
        assert!(parse_netstat_line("en0 1500 <Link#1> x y").is_none());
        assert!(parse_netstat_line("en0 1500 <Link#14> aa:bb 1 0 notnum 1 0 5 0").is_none());
    }

    #[test]
    fn wifi_plus_ethernet_candidates_en0_first_idle_skipped() {
        assert_eq!(gateway_candidates(&all()), ["en0", "en5"]); // en4 carried nothing
    }

    #[test]
    fn route_gateway_parse() {
        let out = "   route to: default\ndestination: default\n    gateway: 192.168.68.1\n  interface: en0\n";
        let gw = out.lines().find_map(parse_route_gateway);
        assert_eq!(gw, Some("192.168.68.1".parse().unwrap()));
    }

    #[test]
    fn route_offline_or_non_ip_gateway_is_none() {
        assert_eq!(
            "route: writing to routing socket: not in table"
                .lines()
                .find_map(parse_route_gateway),
            None
        );
        assert_eq!(parse_route_gateway("    gateway: link#14"), None);
        assert_eq!(parse_route_gateway(""), None);
    }

    #[test]
    fn vpn_up_still_resolves_the_physical_gateway() {
        // `-ifscope en0` output is independent of the (utun) default route.
        let scoped = "    gateway: 10.0.0.1\n  interface: en0\n";
        assert!(scoped.lines().find_map(parse_route_gateway).is_some());
        // and the tunnel is never a candidate
        assert!(!gateway_candidates(&all()).contains(&"utun4"));
    }

    #[test]
    fn arp_mac_is_zero_padded_and_lowercase() {
        assert_eq!(
            parse_arp_mac("? (192.168.68.1) at a4:b:C:d:e:f on en0 ifscope [ethernet]"),
            Some("a4:0b:0c:0d:0e:0f".into())
        );
    }

    #[test]
    fn arp_incomplete_or_missing_is_none() {
        assert_eq!(
            parse_arp_mac("? (192.168.68.1) at (incomplete) on en0 ifscope [ethernet]"),
            None
        );
        assert_eq!(
            parse_arp_mac("192.168.68.9 (192.168.68.9) -- no entry"),
            None
        );
        assert_eq!(parse_arp_mac(""), None);
    }

    #[test]
    fn normalize_mac_rejects_junk() {
        assert_eq!(normalize_mac("a:b:c"), None);
        assert_eq!(normalize_mac("gg:00:00:00:00:00"), None);
        assert_eq!(normalize_mac("001:00:00:00:00:00"), None);
        assert_eq!(
            normalize_mac("AA:BB:CC:DD:EE:FF"),
            Some("aa:bb:cc:dd:ee:ff".into())
        );
    }

    #[test]
    fn boottime_parse() {
        let s = "{ sec = 1795002037, usec = 115010 } Wed Nov 18 18:40:37 2026";
        assert_eq!(parse_boottime(s), Some(1795002037));
        assert_eq!(parse_boottime("garbage"), None);
    }
}
