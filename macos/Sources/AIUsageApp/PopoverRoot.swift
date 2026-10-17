import AIUsageCore
import AIUsageDesign
import AppKit
import SwiftUI

enum PopoverScreen: Equatable {
    case detail(String)
    case monitoring
    case settings
}

/// Popover: 420 pt wide, height ≤ min(780, screen − 96). Header, status row,
/// notice and footer stay pinned; only the card list (or screen body) scrolls.
/// Detail, Monitoring and Settings replace the overview; the overview stays
/// mounted underneath so Back restores its scroll position and focus.
struct PopoverRoot: View {
    @Environment(AppStore.self) private var store
    @Environment(Navigation.self) private var navigation
    @Environment(\.openWindow) private var openWindow
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var screen: PopoverScreen?
    @State private var returnFocus: String?
    /// macOS gives the first control keyboard focus whenever the popover opens.
    /// Rings show only while the keyboard is in use, like a web page's
    /// :focus-visible: any key press shows them, a mouse click or a new open
    /// hides them again.
    @State private var focusRingsVisible = false
    private let detailMetric: ProviderDetailView.Metric
    private let screenHeight: CGFloat?

    init(initialScreen: PopoverScreen? = nil, detailMetric: ProviderDetailView.Metric = .tokens, screenHeight: CGFloat? = nil) {
        _screen = State(initialValue: initialScreen)
        self.detailMetric = detailMetric
        self.screenHeight = screenHeight
    }

    private var maxHeight: CGFloat {
        let screen = screenHeight ?? NSScreen.main?.visibleFrame.height ?? 900
        return min(T.Layout.popoverMaxHeight, screen - T.Layout.popoverScreenMargin)
    }

    var body: some View {
        // Ticks once a minute so relative times ("Checked 2 min ago") stay live between report refreshes.
        TimelineView(.everyMinute) { _ in content }
    }

    private var content: some View {
        ZStack {
            OverviewView(openDetail: { screen = .detail($0) }, openHistory: showHistory,
                         openMonitoring: { screen = .monitoring }, openSettings: { screen = .settings },
                         returnFocus: $returnFocus, focusRingsVisible: $focusRingsVisible, maxHeight: maxHeight)
                .opacity(screen == nil ? 1 : 0)
                .allowsHitTesting(screen == nil)
                .accessibilityHidden(screen != nil)
            switch screen {
            case let .detail(id)?:
                if let provider = store.report?.provider(id) {
                    ProviderDetailView(provider: provider, back: back, openHistory: { showHistory(id) },
                                       maxHeight: maxHeight, initialMetric: detailMetric)
                        .transition(transition)
                }
            case .monitoring?:
                MonitoringView(back: back, openDetail: { screen = .detail($0) }, maxHeight: maxHeight)
                    .transition(transition)
            case .settings?:
                SettingsView(back: back, maxHeight: maxHeight, focusProvider: navigation.settingsRequest?.providerId)
                    .transition(transition)
            case nil:
                EmptyView()
            }
        }
        .frame(width: T.Layout.popoverWidth)
        .background(T.Color.background.color)
        .animation(reduceMotion ? nil : .easeOut(duration: T.Duration.medium), value: screen)
        // A `.window` MenuBarExtra keeps its content alive between opens, so
        // `.onAppear` runs only once; the panel becoming key marks each open.
        .background(PanelShownObserver { focusRingsVisible = false; store.popoverOpened() })
        .focusEffectDisabled(!focusRingsVisible)
        .environment(\.focusRingsVisible, focusRingsVisible)
        .onKeyPress(phases: .down) { _ in
            // Keys a screen handles itself (arrows) never get here; it shows the rings.
            focusRingsVisible = true
            return .ignored
        }
        .background(MouseDownObserver { focusRingsVisible = false })
        // History's "Provider settings…" opens the popover on Settings,
        // including a request made before the popover was first shown.
        .onChange(of: navigation.settingsRequest, initial: true) { _, request in
            if request != nil { screen = .settings }
        }
        .background {
            // ⌘, opens Settings from anywhere in the popover.
            Button("") { screen = .settings }.keyboardShortcut(",", modifiers: .command).hidden()
        }
    }

    private var transition: AnyTransition {
        reduceMotion ? .identity : .move(edge: .trailing).combined(with: .opacity)
    }

    private func back() {
        if case let .detail(id)? = screen { returnFocus = id }
        navigation.settingsRequest = nil
        screen = nil
    }

    private func showHistory(_ providerId: String?) {
        navigation.historyRequest = HistoryRequest(providerId: providerId)
        openWindow(id: HistoryView.windowId)
        NSApp.activate(ignoringOtherApps: true)
    }
}

/// Measures a view's height into a binding (used to size the scroll region to content).
struct HeightReader: ViewModifier {
    @Binding var height: CGFloat
    func body(content: Content) -> some View {
        content.background(GeometryReader { proxy in
            Color.clear
                .onAppear { height = proxy.size.height }
                .onChange(of: proxy.size.height) { _, value in height = value }
        })
    }
}

extension View {
    func readHeight(_ height: Binding<CGFloat>) -> some View { modifier(HeightReader(height: height)) }
}

/// Calls `onShow` each time the hosting panel becomes key (every popover open).
struct PanelShownObserver: NSViewRepresentable {
    let onShow: () -> Void

    func makeNSView(context: Context) -> ObserverView { ObserverView(onShow: onShow) }
    func updateNSView(_ view: ObserverView, context: Context) { view.onShow = onShow }

    final class ObserverView: NSView {
        var onShow: () -> Void
        private var token: NSObjectProtocol?

        init(onShow: @escaping () -> Void) {
            self.onShow = onShow
            super.init(frame: .zero)
        }

        required init?(coder: NSCoder) { nil }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let token { NotificationCenter.default.removeObserver(token) }
            token = nil
            guard let window else { return }
            token = NotificationCenter.default.addObserver(
                forName: NSWindow.didBecomeKeyNotification, object: window, queue: .main
            ) { [weak self] _ in self?.onShow() }
            if window.isKeyWindow { onShow() }
        }

        deinit { if let token { NotificationCenter.default.removeObserver(token) } }
    }
}

/// Whether keyboard focus rings should be drawn in this window. The popover and
/// History set it: rings show while the keyboard is in use and hide on a click
/// or when the window comes forward. Defaults to true elsewhere.
private struct FocusRingsVisibleKey: EnvironmentKey { static let defaultValue = true }

extension EnvironmentValues {
    var focusRingsVisible: Bool {
        get { self[FocusRingsVisibleKey.self] }
        set { self[FocusRingsVisibleKey.self] = newValue }
    }
}

/// Calls `onMouseDown` for every mouse click in this app's windows while the
/// view is mounted (a local monitor: it only observes, never consumes, events).
struct MouseDownObserver: NSViewRepresentable {
    let onMouseDown: () -> Void

    func makeNSView(context: Context) -> MonitorView { MonitorView(onMouseDown: onMouseDown) }
    func updateNSView(_ view: MonitorView, context: Context) { view.onMouseDown = onMouseDown }

    final class MonitorView: NSView {
        var onMouseDown: () -> Void
        private var monitor: Any?

        init(onMouseDown: @escaping () -> Void) {
            self.onMouseDown = onMouseDown
            super.init(frame: .zero)
        }

        required init?(coder: NSCoder) { nil }

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let monitor { NSEvent.removeMonitor(monitor) }
            monitor = nil
            guard window != nil else { return }
            monitor = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] event in
                if event.window === self?.window { self?.onMouseDown() }
                return event
            }
        }

        deinit { if let monitor { NSEvent.removeMonitor(monitor) } }
    }
}
