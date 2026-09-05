"""Repositories for the HR domain.

Every one of these is `BaseRepository` plus two class attributes: the model,
and the `public_id` prefix from Architecture §2. That is the whole file, and
deliberately so — Phase 0 builds the skeleton, and a repository that adds a
custom query before anything needs it is a guess about a query shape.

Grouped in one module rather than split one-per-file because there is nothing
in any of them to read: fourteen four-line files would make the prefix table
harder to check against Architecture §2, not easier. When a repository grows
real query logic (Phase 1 gives EmployeeRepository and ContractRepository
theirs), it moves to its own module.

THE PREFIXES ARE LEAD, NOT PENCIL. `public_id` is the only identifier that ever
leaves the server, and `decode_public_id` refuses to decode a hashid under the
wrong prefix — which is what stops `pslip_xyz` resolving to a real Employee row
and showing someone a colleague's pay. Changing a prefix after any row exists
invalidates every id already handed out.
"""
from app.models.attendance import Attendance
from app.models.contract import Contract
from app.models.department import Department
from app.models.employee import Employee
from app.models.payroll import Payrun, PayrunEmployee, Payslip, PayslipLine
from app.models.salary import SalaryRule, SalaryStructure, SalaryStructureRule
from app.models.time_off import TimeOffAllocation, TimeOffRequest, TimeOffType
from app.models.working_schedule import ScheduleLine, WorkingSchedule
from app.repositories.base import BaseRepository


class DepartmentRepository(BaseRepository[Department]):
    model = Department
    public_id_prefix = "dept"


class EmployeeRepository(BaseRepository[Employee]):
    model = Employee
    public_id_prefix = "emp"


class ContractRepository(BaseRepository[Contract]):
    model = Contract
    public_id_prefix = "ctr"


class WorkingScheduleRepository(BaseRepository[WorkingSchedule]):
    model = WorkingSchedule
    public_id_prefix = "wsch"


class ScheduleLineRepository(BaseRepository[ScheduleLine]):
    """A line-item child, so it has no version/audit columns — BaseRepository's
    `update`/`soft_delete` are unusable here and lines are always rewritten
    through their parent schedule instead. Present for `create`'s public_id
    minting and for id decoding."""

    model = ScheduleLine
    public_id_prefix = "sline"


class AttendanceRepository(BaseRepository[Attendance]):
    model = Attendance
    public_id_prefix = "att"


class TimeOffTypeRepository(BaseRepository[TimeOffType]):
    model = TimeOffType
    public_id_prefix = "tot"


class TimeOffAllocationRepository(BaseRepository[TimeOffAllocation]):
    model = TimeOffAllocation
    public_id_prefix = "alloc"


class TimeOffRequestRepository(BaseRepository[TimeOffRequest]):
    model = TimeOffRequest
    public_id_prefix = "req"


class SalaryStructureRepository(BaseRepository[SalaryStructure]):
    model = SalaryStructure
    public_id_prefix = "sstr"


class SalaryStructureRuleRepository(BaseRepository[SalaryStructureRule]):
    """Line-item child; see ScheduleLineRepository."""

    model = SalaryStructureRule
    public_id_prefix = "sstrule"


class SalaryRuleRepository(BaseRepository[SalaryRule]):
    model = SalaryRule
    public_id_prefix = "srule"


class PayrunRepository(BaseRepository[Payrun]):
    model = Payrun
    public_id_prefix = "prun"


class PayrunEmployeeRepository(BaseRepository[PayrunEmployee]):
    """Line-item child; see ScheduleLineRepository."""

    model = PayrunEmployee
    public_id_prefix = "prunemp"


class PayslipRepository(BaseRepository[Payslip]):
    model = Payslip
    public_id_prefix = "pslip"


class PayslipLineRepository(BaseRepository[PayslipLine]):
    """Line-item child; see ScheduleLineRepository."""

    model = PayslipLine
    public_id_prefix = "psline"


#: Every prefix in one place, so a collision is a test failure rather than a
#: production id that silently decodes to the wrong table.
#: See tests/test_domain_skeleton.py.
ALL_REPOSITORIES = (
    DepartmentRepository,
    EmployeeRepository,
    ContractRepository,
    WorkingScheduleRepository,
    ScheduleLineRepository,
    AttendanceRepository,
    TimeOffTypeRepository,
    TimeOffAllocationRepository,
    TimeOffRequestRepository,
    SalaryStructureRepository,
    SalaryStructureRuleRepository,
    SalaryRuleRepository,
    PayrunRepository,
    PayrunEmployeeRepository,
    PayslipRepository,
    PayslipLineRepository,
)
