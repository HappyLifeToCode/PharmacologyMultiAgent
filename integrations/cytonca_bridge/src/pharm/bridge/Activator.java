package pharm.bridge;

import java.lang.reflect.Field;
import java.util.*;
import org.osgi.framework.*;
import org.cytoscape.model.*;
import org.cytoscape.work.*;
import org.cytoscape.work.json.JSONResult;

/** Version-pinned adapter: invokes the installed app, never reimplements DC. */
public final class Activator implements BundleActivator {
    private ServiceRegistration<?> registration;
    public void start(BundleContext context) {
        Hashtable<String, Object> props = new Hashtable<>();
        props.put("commandNamespace", "pharmCytoNCA");
        props.put("command", "degree");
        props.put("commandDescription", "CytoNCA 2.1.6 unweighted DC compatibility bridge");
        props.put("commandSupportsJSON", "true");
        registration = context.registerService(TaskFactory.class, new AbstractTaskFactory() {
            public TaskIterator createTaskIterator() { return new TaskIterator(new DegreeTask(context)); }
        }, props);
    }
    public void stop(BundleContext context) { if (registration != null) registration.unregister(); }

    public static final class DegreeTask extends AbstractTask implements ObservableTask {
        @Tunable(description="Exact network SUID", required=true)
        public Long network;
        private final BundleContext context;
        private String result = "{}";
        DegreeTask(BundleContext context) { this.context = context; }

        public void run(TaskMonitor monitor) throws Exception {
            ServiceReference<CyNetworkManager> managerRef = context.getServiceReference(CyNetworkManager.class);
            try {
                CyNetwork net = context.getService(managerRef).getNetwork(network);
                if (net == null) throw new IllegalArgumentException("Unknown network SUID");
                // Protect unrelated interactive networks and reject unsupported graph semantics.
                if (net.getDefaultNetworkTable().getColumn("pharm_bridge_input") == null ||
                    !Boolean.TRUE.equals(net.getRow(net).get("pharm_bridge_input", Boolean.class)))
                    throw new IllegalArgumentException("Network must be created by the pharmacology adapter");
                Set<String> pairs = new HashSet<>();
                for (CyEdge edge : net.getEdgeList()) {
                    long a = edge.getSource().getSUID(), b = edge.getTarget().getSUID();
                    if (edge.isDirected() || a == b || !pairs.add(Math.min(a,b)+":"+Math.max(a,b)))
                        throw new IllegalArgumentException("Only simple undirected graphs are supported");
                }
                Object action = null;
                ServiceReference<?> actionRef = null;
                Bundle plugin = null;
                for (Bundle bundle : context.getBundles()) {
                    if (bundle.getState() != Bundle.ACTIVE) continue;
                    ServiceReference<?>[] refs = bundle.getRegisteredServices();
                    if (refs == null) continue;
                    for (ServiceReference<?> ref : refs) {
                        Object service = context.getService(ref);
                        if (service != null && service.getClass().getName().equals(
                                "org.cytoscape.CytoNCA.internal.actions.AnalyzeAction")) {
                            action = service; actionRef = ref; plugin = bundle; break;
                        }
                        if (service != null) context.ungetService(ref);
                    }
                    if (action != null) break;
                }
                if (action == null) throw new IllegalStateException("Active CytoNCA AnalyzeAction not found");
                try {
                    if (!plugin.getVersion().toString().equals("2.1.6"))
                        throw new IllegalStateException("Bridge supports CytoNCA 2.1.6 only");
                    Field field = action.getClass().getDeclaredField("pUtil");
                    field.setAccessible(true);
                    Object util = field.get(action);
                    String prefix = "org.cytoscape.CytoNCA.internal.";
                    Class<?> proteinClass = plugin.loadClass(prefix + "Protein");
                    Class<?> utilClass = plugin.loadClass(prefix + "ProteinUtil");
                    Class<?> dcClass = plugin.loadClass(prefix + "algorithm.DC");
                    ArrayList<Object> proteins = new ArrayList<>();
                    for (CyNode node : net.getNodeList())
                        proteins.add(proteinClass.getConstructor(CyNode.class, CyNetwork.class).newInstance(node, net));
                    // Null parameter ID obtains a fresh ParameterSet; explicit run(false) selects unweighted DC.
                    Object dc = dcClass.getConstructor(Long.class, utilClass).newInstance(null, util);
                    dcClass.getMethod("run", CyNetwork.class, ArrayList.class, boolean.class).invoke(dc, net, proteins, false);
                    if (cancelled) throw new IllegalStateException("Cancelled");
                    CyTable table = net.getDefaultNodeTable();
                    if (table.getColumn("CytoNCA_DC") != null)
                        throw new IllegalStateException("Refusing to overwrite previous topology");
                    table.createColumn("CytoNCA_DC", Double.class, false);
                    for (Object protein : proteins) {
                        CyNode node = (CyNode) proteinClass.getMethod("getN").invoke(protein);
                        double value = (Double) proteinClass.getMethod("getDC").invoke(protein);
                        if (!Double.isFinite(value)) throw new IllegalStateException("Nonfinite CytoNCA result");
                        net.getRow(node).set("CytoNCA_DC", value);
                    }
                    result = "{\"bridgeVersion\":\"0.1.0\",\"cytoncaVersion\":\"2.1.6\","
                        + "\"implementation\":\"org.cytoscape.CytoNCA.internal.algorithm.DC.run\","
                        + "\"weighted\":false,\"networkSUID\":" + network + ",\"nodeCount\":" + proteins.size() + "}";
                } finally { context.ungetService(actionRef); }
            } finally { if (managerRef != null) context.ungetService(managerRef); }
        }
        public List<Class<?>> getResultClasses() { return Arrays.asList(String.class, JSONResult.class); }
        public <R> R getResults(Class<? extends R> type) {
            if (type.equals(JSONResult.class)) return type.cast((JSONResult) () -> result);
            return type.cast(result);
        }
    }
}
