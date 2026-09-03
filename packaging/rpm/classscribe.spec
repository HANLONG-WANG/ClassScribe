Name:           classscribe
Version:        0.1.0
Release:        1%{?dist}
Summary:        Local-first classroom transcription and voice input
License:        LicenseRef-ClassScribe-NOASSERTION
URL:            https://classscribe.local/
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.12
Requires:       python3dist(alembic)
Requires:       python3dist(fastapi)
Requires:       python3dist(msgspec)
Requires:       python3dist(opencc-python-reimplemented)
Requires:       python3dist(pydantic)
Requires:       python3dist(pyyaml)
Requires:       python3dist(sqlalchemy)
Requires:       python3dist(uvicorn)
Requires:       curl
Requires:       ffmpeg
Requires:       bubblewrap
Requires:       ibus
Requires:       pipewire
Requires:       python3-gobject
Requires:       uv
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
Requires:       systemd
Requires:       wireplumber
Requires:       xdg-desktop-portal
Requires:       xdg-utils

%description
ClassScribe is a local-first classroom transcription workbench and IBus voice
input method. Model artifacts and all user data are installed separately in
the current user's XDG directories and are intentionally excluded from RPM.

%prep
%autosetup

%build

%install
install -d %{buildroot}/usr/lib/classscribe
cp -a backend/classscribe %{buildroot}/usr/lib/classscribe/
cp -a protocol/python/classscribe_protocol %{buildroot}/usr/lib/classscribe/
cp -a ibus/engine/classscribe_ibus_engine %{buildroot}/usr/lib/classscribe/
cp -a ibus/dictationd/classscribe_dictationd %{buildroot}/usr/lib/classscribe/
cp -a ibus/hotkey_portal/classscribe_hotkey_portal %{buildroot}/usr/lib/classscribe/
cp -a scripts %{buildroot}/usr/lib/classscribe/

install -Dpm0755 packaging/rpm/classscribe-core %{buildroot}%{_libexecdir}/classscribe-core
install -Dpm0755 packaging/rpm/classscribe-dictationd %{buildroot}%{_libexecdir}/classscribe-dictationd
install -Dpm0755 packaging/rpm/classscribe-hotkey %{buildroot}%{_libexecdir}/classscribe-hotkey
install -Dpm0755 packaging/rpm/ibus-engine-classscribe %{buildroot}%{_libexecdir}/ibus-engine-classscribe
install -Dpm0755 packaging/rpm/classscribe-benchmark %{buildroot}%{_bindir}/classscribe-benchmark
install -Dpm0755 packaging/rpm/classscribe-release-check %{buildroot}%{_bindir}/classscribe-release-check
install -Dpm0755 packaging/rpm/classscribe-doctor %{buildroot}%{_bindir}/classscribe-doctor
install -Dpm0755 packaging/desktop/classscribe-launcher %{buildroot}%{_libexecdir}/classscribe-launcher

install -Dpm0644 packaging/systemd/classscribe-core.service %{buildroot}/usr/lib/systemd/user/classscribe-core.service
install -Dpm0644 packaging/systemd/classscribe-dictationd.service %{buildroot}/usr/lib/systemd/user/classscribe-dictationd.service
install -Dpm0644 packaging/systemd/classscribe-hotkey.service %{buildroot}/usr/lib/systemd/user/classscribe-hotkey.service
install -Dpm0644 packaging/desktop/classscribe.desktop %{buildroot}%{_datadir}/applications/classscribe.desktop
install -Dpm0644 packaging/desktop/classscribe.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/classscribe.svg
install -Dpm0644 ibus/component/classscribe.xml %{buildroot}%{_datadir}/ibus/component/classscribe.xml
install -Dpm0644 config/default.yaml %{buildroot}%{_datadir}/classscribe/default.yaml
cp -a config %{buildroot}%{_datadir}/classscribe/
cp -a protocol/schema %{buildroot}%{_datadir}/classscribe/protocol-schema
install -d %{buildroot}%{_datadir}/classscribe/protocol
cp -a protocol/python %{buildroot}%{_datadir}/classscribe/protocol/
cp -a workers %{buildroot}%{_datadir}/classscribe/
cp -a benchmarks %{buildroot}%{_datadir}/classscribe/
install -d %{buildroot}%{_datadir}/classscribe/frontend
cp -a frontend/dist %{buildroot}%{_datadir}/classscribe/frontend/
cp -a release %{buildroot}%{_datadir}/classscribe/
install -d %{buildroot}%{_datadir}/classscribe/tests/audio_fixtures
install -d %{buildroot}%{_datadir}/classscribe/tests/golden
install -pm0644 tests/audio_fixtures/regression-audio.v1.json %{buildroot}%{_datadir}/classscribe/tests/audio_fixtures/
install -pm0644 tests/golden/regressions.v1.json %{buildroot}%{_datadir}/classscribe/tests/golden/
install -d %{buildroot}%{_docdir}/%{name}
cp -a docs %{buildroot}%{_docdir}/%{name}/
install -Dpm0644 README.md %{buildroot}%{_docdir}/%{name}/README.md
install -Dpm0644 LICENSES/README.md %{buildroot}%{_licensedir}/%{name}/README.md
install -Dpm0644 LICENSES/inventory.v1.json %{buildroot}%{_licensedir}/%{name}/inventory.v1.json

%files
%license %{_licensedir}/%{name}/README.md
%license %{_licensedir}/%{name}/inventory.v1.json
%doc %{_docdir}/%{name}/README.md
%doc %{_docdir}/%{name}/docs/
/usr/lib/classscribe/
%{_libexecdir}/classscribe-core
%{_libexecdir}/classscribe-dictationd
%{_libexecdir}/classscribe-hotkey
%{_libexecdir}/ibus-engine-classscribe
%{_libexecdir}/classscribe-launcher
%{_bindir}/classscribe-benchmark
%{_bindir}/classscribe-release-check
%{_bindir}/classscribe-doctor
/usr/lib/systemd/user/classscribe-core.service
/usr/lib/systemd/user/classscribe-dictationd.service
/usr/lib/systemd/user/classscribe-hotkey.service
%{_datadir}/applications/classscribe.desktop
%{_datadir}/icons/hicolor/scalable/apps/classscribe.svg
%{_datadir}/ibus/component/classscribe.xml
%{_datadir}/classscribe/

%changelog
* Thu Sep 03 2026 ClassScribe Developers <devnull@classscribe.local> - 0.1.0-1
- Initial ordinary-user service and desktop integration skeleton
